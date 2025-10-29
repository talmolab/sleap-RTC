# sleap-RTC
- Remote training and inference w/ SLEAP
- Remote Authenticated CLI Training w/ SLEAP

## Configuration

SLEAP-RTC supports flexible configuration for different deployment environments (development, staging, production).

### Configuration Priority

Configuration is loaded in the following priority order (highest to lowest):

1. **CLI arguments** - Explicit command-line flags like `--server`
2. **Environment variables** - `SLEAP_RTC_SIGNALING_WS`, `SLEAP_RTC_SIGNALING_HTTP`
3. **Configuration file** - TOML file with environment-specific settings
4. **Defaults** - Production signaling server

### Environment Selection

Set the environment using the `SLEAP_RTC_ENV` environment variable:

```bash
export SLEAP_RTC_ENV=development  # Use development environment
export SLEAP_RTC_ENV=staging      # Use staging environment
export SLEAP_RTC_ENV=production   # Use production environment (default)
```

Valid environments: `development`, `staging`, `production`

### Configuration File

Create a configuration file at one of these locations:
- `sleap-rtc.toml` in your project directory
- `~/.sleap-rtc/config.toml` in your home directory

See `config.example.toml` for a complete example with all environments.

Example configuration:

```toml
[default]
# Shared settings across all environments
connection_timeout = 30
chunk_size = 65536

[environments.development]
signaling_websocket = "ws://localhost:8080"
signaling_http = "http://localhost:8001"

[environments.staging]
signaling_websocket = "ws://staging-server.example.com:8080"
signaling_http = "http://staging-server.example.com:8001"

[environments.production]
signaling_websocket = "ws://ec2-54-176-92-10.us-west-1.compute.amazonaws.com:8080"
signaling_http = "http://ec2-54-176-92-10.us-west-1.compute.amazonaws.com:8001"
```

### Environment Variable Overrides

Override specific settings using environment variables:

```bash
# Override WebSocket URL
export SLEAP_RTC_SIGNALING_WS="ws://custom-server.com:8080"

# Override HTTP API URL
export SLEAP_RTC_SIGNALING_HTTP="http://custom-server.com:8001"
```

### Usage Examples

```bash
# Use default production environment
sleap-rtc train data.slp

# Use development environment
SLEAP_RTC_ENV=development sleap-rtc train data.slp

# Use staging environment
SLEAP_RTC_ENV=staging sleap-rtc train data.slp

# Override with environment variable
SLEAP_RTC_SIGNALING_WS=ws://custom.com:8080 sleap-rtc train data.slp

# Override with CLI argument
sleap-rtc train data.slp --server ws://custom.com:8080
```

### Backward Compatibility

If no configuration is provided, SLEAP-RTC defaults to the production signaling server, maintaining backward compatibility with existing deployments.
