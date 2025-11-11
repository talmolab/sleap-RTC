# Configuration Files

This directory contains example configuration files for SLEAP-RTC.

## Files

- **config.example.toml** - Example configuration file showing all available options
- **sleap-rtc.toml** - Project-specific configuration (gitignored)

## Using Configuration Files

SLEAP-RTC looks for configuration files in the following order:

1. `sleap-rtc.toml` in your current working directory
2. `~/.sleap-rtc/config.toml` in your home directory

### Quick Start

Copy the example configuration to get started:

```bash
# For project-level config
cp config/config.example.toml sleap-rtc.toml

# For user-level config
mkdir -p ~/.sleap-rtc
cp config/config.example.toml ~/.sleap-rtc/config.toml
```

Then edit the configuration file to set your environment and signaling server URLs.

## Configuration Options

See `config.example.toml` for a complete list of available configuration options and their descriptions.

## Environment Variables

Configuration can also be set via environment variables:
- `SLEAP_RTC_ENV` - Environment name (development, staging, production)
- `SLEAP_RTC_SIGNALING_WS` - WebSocket signaling server URL
- `SLEAP_RTC_SIGNALING_HTTP` - HTTP signaling server URL

Environment variables take precedence over configuration file settings.
