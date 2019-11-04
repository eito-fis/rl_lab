
import os
from collections import deque

import numpy as np
import tensorflow as tf

from src.general.envs.parallel_env import ParallelEnv
from src.sac.sac_model import SACQnet, SACActor
from src.sac.sac_policy import SACPolicy
from src.general.policies import RandomPolicy
from src.general.replay_buffers.replay_buffer import ReplayBuffer


class SACAgent():
    '''
    SAC Agent class. Builds and trains a model
    train_steps: Number of episodes to play and train on
    learning_rate: Learning_rate
    num_steps: Number of steps for each environment to take per rollout
    env_func: Function that builds one instance of an environment. Will be
    passed an idx as arg
    num_envs: Number of environments to run in parallel
    actor_fc: Actor model dense layers topology
    critic_fc: Critic model dense layers topology
    conv_size: Conv model topology
    '''
    def __init__(self,
                 train_steps=None,
                 random_steps=None,
                 train_freq=1,
                 actor_lr=0.0042,
                 q_lr=0.0042,
                 entropy_lr=0.0042,
                 gamma=0.99,
                 alpha=None,
                 buffer_size=50000,
                 batch_size=64,
                 gradient_steps=1,
                 env_func=None,
                 num_envs=None,
                 actor_fc=None,
                 critic_fc=None,
                 conv_size=None,
                 logging_period=25,
                 checkpoint_period=50,
                 output_dir="/tmp/sac",
                 restore_dir=None):

        # Build environment
        env_func_list = [env_func for _ in range(num_envs)]
        self.env = ParallelEnv(env_func_list)
        self.obs, self.infos = self.env.reset()

        # Build models
        self.actor = SACActor(num_actions=num_actions,
                              state_size=self.env.state_size,
                              stack_size=self.env.stack_size,
                              action_space=self.env.action_space,
                              fc=actor_fc,
                              conv_size=conv_size)
        self.q1 = SACQnet(num_actions=num_actions,
                          state_size=self.env.state_size,
                          stack_size=self.env.stack_size,
                          action_space=self.env.action_space,
                          fc=critic_fc,
                          conv_size=conv_size)
        self.q2 = SACQnet(num_actions=num_actions,
                          state_size=self.env.state_size,
                          stack_size=self.env.stack_size,
                          action_space=self.env.action_space,
                          fc=critic_fc,
                          conv_size=conv_size)
        self.q1_t = tf.keras.models.clone_model(self.q1)
        self.q2_t = tf.keras.models.clone_model(self.q2)
        if restore_dir != None:
            self.model.load_weights(restore_dir)

        # Build policy, replay buffer and optimizers
        self.policy = SACPolicy(action_space=self.env.action_space,
                                batch_size=self.num_env,
                                model=self.actor)
        self.random_policy = RandomPolicy(action_space=self.env.action_space,
                                          batch_size=self.num_env)
        self.replay_buffer = ReplayBuffer(buffer_size)
        self.actor_opt = tf.keras.optimizers.Adam(actor_lr)
        self.q1_opt = tf.keras.optimizers.Adam(q_lr)
        self.q2_opt = tf.keras.optimizers.Adam(q_lr)
        self.entropy_opt = tf.keras.optimizers.Adam(entropy_lr)

        # Setup training parameters
        self.gamma = gamma
        self.alpha = alpha
        self.train_steps = train_steps
        self.random_steps = random_steps
        self.train_freq = train_freq
        self.batch_size = batch_size
        self.gradient_steps = gradient_steps

        # Setup logging parameters
        self.floor_queue = deque(maxlen=100)
        self.reward_queue = deque(maxlen=100)
        self.logging_period = logging_period
        self.checkpoint_period = checkpoint_period
        self.episodes = 0

        # Build logging directories
        self.log_dir = os.path.join(output_dir, "logs/")
        os.makedirs(os.path.dirname(self.log_dir), exist_ok=True)
        self.checkpoint_dir = os.path.join(output_dir, "checkpoints/")
        os.makedirs(os.path.dirname(self.checkpoint_dir), exist_ok=True)

        # Build summary writer
        self.summary_writer = tf.summary.create_file_writer(self.log_dir)

    def train(self):
        for i in range(self.train_steps):
            if i < self.random_steps:
                actions = self.random_policy()
            else:
                actions, _ = self.policy(self.obs)

            # Take step on env with action
            new_obs, rewards, self.dones, self.infos = self.env.step(actions)

            # Store SARS(D) in replay buffer
            self.replay_buffer.add(self.obs, action, rewards, new_obs,
                                   float(self.done))

            # Periodically learn
            if i % self.train_freq == 0:
                for g in self.gradient_steps:
                    # Don't train if the buffer is not full enough or if we are
                    # still collecting random samples
                    if not self.replay_buffer.can_sample(self.batch_size) or \
                       i < self.random_steps:
                        break

                    # Sample and unpack batch
                    batch = self.replay_buffer.sample(self.batch_size)
                    b_obs, b_actions, b_rewards, b_n_obs, b_dones = batch
                    b_obs = process_inputs(b_obs)

                    # Calculate loss
                    b_n_actions, n_log_probs = self.policy(b_n_obs)
                    q1_ts = self.q1_t(b_n_obs, b_n_actions)
                    q2_ts = self.q2_t(b_n_obs, b_n_actions)
                    
                    min_q_ts = tf.minimum(q1_ts, q2_ts) - self.alpha * \
                            n_log_probs
                    target_q = tf.stop_gradient(b_rewards + (1 - b_dones) *
                                                self.gamma * min_q_ts)
                    with tf.GradientTape(persistent=True) as tape:
                        # Q loss
                        q1s = self.q1(b_obs, b_actions)
                        q2s = self.q2(b_obs, b_actions)
                        q1_loss = 0.5 * tf.reduce_mean((q1s - target_q) ** 2)
                        q2_loss = 0.5 * tf.reduce_mean((q2s - target_q) ** 2)

                        # Policy loss
                        new_actions, log_probs = self.policy(b_obs)
                        n_q1s = self.q1(b_obs, new_actions)
                        n_q2s = self.q2(b_obs, new_actions)
                        min_n_qs = tf.min(q1s, q2s)

                        policy_loss = tf.reduce_mean(self.alpha * log_probs - \
                                                     min_n_qs)

                    # Calculate and apply gradients
                    q1_grad = tape.gradient(q1_loss, self.q1.trainable_weights)
                    self.q1_opt.apply_gradients(zip(q1_grad,
                                                    self.q1.trianable_weights))
                    q2_grad = tape.gradient(q2_loss, self.q2.trainable_weights)
                    self.q2_opt.apply_gradients(zip(q2_grad,
                                                    self.q2.trianable_weights))
                    actor_grad = tape.gradient(policy_loss,
                                               self.actor.trainable_weights)
                    self.actor_opt.apply_gradients(zip(actor_grad,
                                                       self.actor.trainable_weights))
                    # Delete the Gradient Tape
                    del tape

            # Log data
            self.logging(b_rewards, b_values, ep_infos, entropy_loss,
                         policy_loss, value_loss, i)

            print("\n")

    def logging(self, rewards, values, ep_infos, entropy_loss, policy_loss,
                value_loss, i):
        # Pull specific info from info array and store in queue
        for info in ep_infos:
            self.floor_queue.append(info["floor"])
            self.reward_queue.append(info["total_reward"])
            self.episodes += 1

        avg_floor = 0 if len(self.floor_queue) == 0 else sum(self.floor_queue)/\
        len(self.floor_queue)
        avg_reward = 0 if len(self.reward_queue) == 0 else\
        sum(self.reward_queue) / len(self.reward_queue)
        explained_variance = self.explained_variance(values, rewards)

        print("| Iteration: {} |".format(i))
        print("| Episodes: {} | Average Floor: {} | Average Reward: {}\
              |".format(self.episodes, avg_floor, avg_reward))
        print("| Entropy Loss: {} | Policy Loss: {} | Value Loss: {}\
              |".format(entropy_loss, policy_loss, value_loss))
        print("| Explained Variance: {} | Environment Variance: {}\
              |".format(explained_variance, np.var(rewards)))

        # Periodically log
        if i % self.logging_period == 0:
            with self.summary_writer.as_default():
                tf.summary.scalar("Average Floor", avg_floor, i)
                tf.summary.scalar("Average Reward", avg_reward, i)
                tf.summary.scalar("Entropy Loss", entropy_loss, i)
                tf.summary.scalar("Policy Loss", policy_loss, i)
                tf.summary.scalar("Value Loss", value_loss, i)
                tf.summary.scalar("Explained Variance", explained_variance, i)
        # Periodically save checkoints
        if i % self.checkpoint_period == 0:
            model_save_path = os.path.join(self.checkpoint_dir,
                                           "model_{}.h5".format(i))
            self.model.save_weights(model_save_path)
            print("Model saved to {}".format(model_save_path))

    def explained_variance(self, y_pred, y_true):
        """
        Computes fraction of variance that ypred explains about y.
        Returns 1 - Var[y-ypred] / Var[y]
        interpretation:
            ev=0  =>  might as well have predicted zero
            ev=1  =>  perfect prediction
            ev<0  =>  worse than just predicting zero
        """
        assert y_true.ndim == 1 and y_pred.ndim == 1
        var_y = np.var(y_true)
        return np.nan if var_y == 0 else 1 - np.var(y_true - y_pred) / var_y





if __name__ == '__main__':
    from src.a2c.wrapped_obstacle_tower_env import WrappedObstacleTowerEnv
    env_filename = "../ObstacleTower/obstacletower"
    def env_func(idx):
        return WrappedObstacleTowerEnv(env_filename,
                                       worker_id=idx,
                                       gray_scale=True,
                                       realtime_mode=True)

    print("Building agent...")
    agent = A2CAgent(train_steps=1,
                     entropy_discount=0.01,
                     value_discount=0.5,
                     learning_rate=0.00000042,
                     num_steps=5,
                     env_func=env_func,
                     num_envs=4,
                     num_actions=4,
                     actor_fc=[1024,512],
                     critic_fc=[1024,512],
                     conv_size=((8,4,32), (4,2,64), (3,1,64)),
                     output_dir="./agent_test")
    print("Agent built!")

    print("Starting train...")
    agent.train()
    print("Train done!")
