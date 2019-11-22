import numpy as np
import tensorflow as tf
import tensorflow_probability.distributions as Normal

from src.general.policies.policy import Policy

# GLOBAL
# Prevent division by zero
EPS = 1e-6
# CAP the standard deviation of the actor
LOG_STD_MAX = 2
LOG_STD_MIN = -20

class SACPolicy(Policy):
    """
    SAC Policy

    Attributes:
        action_space: Action space of the policy, expected format depends on the
        action_space_type
        representining minimium and maximium values of that action.
        action_space_type: What type of action space the policy is operating in.
        Current possible values are "Discrete" and "Continuous"
            - Discrete should pass an int as action_space
            - Continuous should pass a list of tuples (min_value, max_value) as
            action_space
        batch_size: Number of actions to be generated at once
        sample_func: Function to use to determine action
        model: Model used for the policy
    """
    def __init__(self,
                 action_space=None,
                 action_space_type="Continuous",
                 batch_size=1,
                 model=None):
        super().__init__(action_space=action_space,
                         action_space_type=action_space_type,
                         batch_size=batch_size)
        self.model = model
        if action_space_type == "Discrete":
            self.eval_func = self.eval_disc
            self.step_func = self.step_disc
        elif action_space_type=="Continuous":
            self.eval_func = self.eval_cont
            self.step_func = self.step_cont


    def eval_disc(self):
        """
        SAC Discrete coming to cloud engines near you...
        """
        raise NotImplementedError

    def step_disc(self):
        """
        SAC Discrete coming to cloud engines near you...
        """
        raise NotImplementedError

    def eval_cont(self, obs, flag):
        """
        Samples actions using the actor network

        Returns:
            actions: List of length of obs, where each element is a list
            containing actions for all dimensions
        """
        mean, log_std = self.model(obs)
        #log_std = tf.clip_by_value(log_std, LOG_STD_MIN, LOG_STD_MAX)
        std = tf.exp(log_std)

        pre_squish_action = mean + std * Normal(0, 1).sample()
        squish_action = tf.math.tanh(pre_squish_action)
        action = squish_action * self.action_range

        log_prob = Normal(mean, std).log_prob(pre_squish_action) - \
                    tf.math.log(1. - squish_action**2 + EPS) - \
                    np.log(self.action_range)
        log_prob = tf.reduce_sum(log_prob, axis=1)[:, None]

        # # log_prob = self.gaussian_prob(action, mean, log_std)
        # log_prob_ns = self.gaussian_prob(action, mean, log_std)
        # log_prob = tf.reduce_sum(log_prob_ns, axis=1)
        # # scaled_action, scaled_log_prob = self.squish(mean, action, log_prob)
        # scaled_action, scaled_log_prob, scaled_log_prob_ns = self.squish(mean, action, log_prob)
        if flag:
            print(f"Mean: {mean}")
            print(f"Std Dev: {std}")
            print(f"Action: {action}")
            print(f"Probs: {tf.exp(log_prob_ns)}")
            print(f"Scaled Action: {scaled_action}")
            print(f"Scaled Probs: {tf.exp(scaled_log_prob_ns)}")
            print(f"Scaled Sum Probs: {tf.exp(scaled_log_prob)}")

        return action, log_prob

    def step_cont(self, obs, deterministic=False):
        mean, log_std = self.model(obs)
        std = tf.exp(log_std)

        pre_squish_action = mean + std * Normal(0, 1).sample()
        pre_squish_action = mean if deterministic else pre_squish_action
        squish_action = tf.math.tanh(pre_squish_action)
        action = squish_action * self.action_range

        return action.numpy()[0]

    def gaussian_prob(self, action, mean, log_std):
        """
        Get log probability of a value given a mean and log standard deviation
        fo a gaussian distribution
        """
        pre_sum = -0.5 * (((action - mean) / (tf.exp(log_std) + EPS)) ** 2 + 2 *
                          log_std + np.log(2 * np.pi)) 
        # log_prob = tf.reduce_sum(pre_sum, axis=1)
        # return log_prob
        return pre_sum

    def squish(self, mean, action, log_prob):
        """
        Squish the action and scale the log probability accordingly
        """
        scaled_action = tf.tanh(action)
        # log_prob -= tf.reduce_sum(tf.math.log(1 - scaled_action ** 2 + EPS),
        #                           axis=1)
        tmp = tf.math.log(1 - scaled_action ** 2 + EPS)
        log_prob -= tf.reduce_sum(tmp, axis=1)
        return scaled_action, log_prob, tmp

    def eval(self, obs, flag=False):
        return self.eval_func(obs, flag)

    def step(self, obs, flag=False):
        return self.step_func(obs, flag)
