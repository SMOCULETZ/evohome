# Home Assistant Custom Component for Honeywell Evotouch

Support for Honeywell (EU-only) Evohome installations: one controller and multiple zones.

NB: this is _for EU-based systems only_ (it will only use the EU-based API).

## Installation instructions

To install this custom component, copy it to `${HASS_CONFIG_DIR}/custom_components/climate/evohome.py`.

The `configuration.yaml` is as below (note `platform: evohome` rather than `platform: honeywell`)...
```
climate:
  - platform: evohome
    username: !secret_evohome_username
    password: !secret_evohome_password
    scan_interval: 300  # this is the recommended minimum
```

## Improvements over the existing Honeywell component

1. Uses v2 of the (EU) API: minimal noticeable benefit as yet, but (sadly) temp precision is reduced from .01°C to .5°C.
2. Exposes the controller as a separate entity (from the zones), and...
3. Correctly assigns operating modes to the controller (e.g. Eco/Away modes) and it's zones (e.g. FollowSchedule/PermanentOverride modes)
4. Greater efficiency: loads all entity in a single `add_devices()` call, and fewer api calls to Honeywell during initialisation.
