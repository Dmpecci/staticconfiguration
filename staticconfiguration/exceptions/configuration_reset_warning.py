
class ConfigurationResetWarning(UserWarning):
    """Exception raised when there is an error resetting the configuration."""

    def __init__(self, message="Configuration file was corrupted, configuration restored to defaults."):
        self.message = message
        super().__init__(self.message)