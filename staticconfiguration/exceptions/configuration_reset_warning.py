# Copyright (c) 2025 David Muñoz Pecci
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

class ConfigurationResetWarning(UserWarning):
    """Exception raised when there is an error resetting the configuration."""

    def __init__(self, message="Configuration file was corrupted, configuration restored to defaults."):
        self.message = message
        super().__init__(self.message)