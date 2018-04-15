# Home Assistant Custom Component for Honeywell Evotouch

Support for Honeywell (EU-only) Evohome installations: one controller and multiple zones.

NB: this is _for EU-based systems only_ (it will only use the EU-based API).

## Installation istructions

To install this custom component, copy it to `${HASS_CONFIG_DIR}/custom_components/climate/evohome.py`.

The `configuration.yaml` as below...
```
climate:
  - platform: evohome
    username: !secret_evohome_username
    password: !secret_evohome_password
    scan_interval: 300
```
