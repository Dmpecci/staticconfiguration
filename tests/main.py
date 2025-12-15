from tests.custom_settings_example import CustomSettings

if __name__ == "__main__":

    print(f"API URL: {CustomSettings.api_url}")
    print(f"Max Retries: {CustomSettings.max_retries}")
    print(f"Enable Feature X: {CustomSettings.enable_feature_x}")