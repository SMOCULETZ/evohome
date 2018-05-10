# Home Assistant Custom Component for Honeywell Evotouch

Support for Honeywell (EU-only) Evohome installations: one controller and multiple zones.

This is beta-level code, YMMV.  It provides functionality that the existing Honeywell climate component does not (mainly 3 & 4, below) and you can run it alongside that component.

NB: this is _for EU-based systems only_, it will not work with US-based systems (it will only use the EU-based API).

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

1. Uses v2 of the (EU) API via evohome-client: minimal noticeable benefit as yet, and (sadly) temp precision is reduced from .1°C to .5°C (but see below).
2. Leverages v1 of the API to increase precision of reported temps to 0.1°C (actually the API reports 0.01, but HA only handles 0.1).  Falls back to v2 temps if unable to get v1 temps. 
3. Exposes the controller as a separate entity (from the zones), and...
4. Correctly assigns operating modes to the controller (e.g. Eco/Away modes) and it's zones (e.g. FollowSchedule/PermanentOverride modes)
5. Greater efficiency: loads all entities in a single `add_devices()` call, and uses fewer api calls to Honeywell during initialisation/polling (i.e. one per location rather than one per zone).

## Problems with current implemenation

1. The controller, which doesn't have a `current_temperature` is implemented as a climate entity, and HA expects all climate entities to report a temperature.  So you will see an empty temperature graph for this entity.  A fix will require: a) changing HA (to accept a climate entity without a temperature (like a fan entity), or; b) changing the controller to a different entity class (but this may break some of the away mode integrations planned for the future).
2. Away mode (as understood by HA), is not implemented as yet - however, you can use service calls to `climate.set_operation_mode` with thw controller or zone entities.
3. The underlying api (evohomeclient2) has some issues (e.g. no provision to refresh OAuth tokens) that requires work-arounds.
4. The code is currently messy, and architecturally unsatisfying (e.g. the controller updates the zones private attributes directly).
5. No provision for DHW.
6. No provision for schedules.
