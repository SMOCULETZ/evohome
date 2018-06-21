# Home Assistant Custom Component for Honeywell Evotouch

Support for Honeywell (EU-only) Evohome installations: one controller and multiple zones.

It provides functionality that the existing Honeywell climate component does not (e.g. 3 & 4, below) and you can run it alongside that component.

This is beta-level code, YMMV.

NB: this is _for EU-based systems only_, it will not work with US-based systems (it will only use the EU-based API).

## Installation instructions (have recently changed)

To install this custom component, copy it to `${HASS_CONFIG_DIR}/custom_components`, for example:
  `git clone https://github.com/zxdavb/evohome ~/.homeassistant/custom_components`

The `configuration.yaml` is as below (note `evohome:` rather than `climate:` & `- platform: honeywell`)...
```
evohome:
  username: !secret evohome_username
  password: !secret evohome_password
# scan_interval: 300  # not currently used - 120 by default
```

Note that `scan_interval` is currently hard-coded to 120 secs.  This is OK (see below).

## Improvements over the existing Honeywell component

1. Uses v2 of the (EU) API via evohome-client: minimal noticeable benefit as yet (also, v2 temperature precision is reduced from .1°C to .5°C, but see the next point).
2. Leverages v1 of the API to increase precision of reported temps to 0.1°C (actually the API reports 0.01, but HA only handles 0.1); falls back to v2 temps if unable to get v1 temps. 
3. BIG: Exposes the controller as a separate entity (from the zones), and...
4. Correctly assigns operating modes to the controller (e.g. Eco/Away modes) and it's zones (e.g. FollowSchedule/PermanentOverride modes)
5. Greater efficiency: loads all entities in a single `add_devices()` call, (TBA) and uses fewer api calls to Honeywell during initialisation/polling.
6. The Controller now only reports its Operating Mode (it previously report a non-existant current/target temperature.

## Problems with current implemenation

0. It takes one scan_interval for the Zones to reflect changes made elsewhere in the location (usu. by the Controller).  A fix is coming for this.
0. Zones will incorrectly report OpenWindowMode (e.g. when Controller is set to HeatingOff).  The hueristics will be improved in future releases.
1. FIXED, option b): The controller, which doesn't have a `current_temperature` is implemented as a climate entity, and HA expects all climate entities to report a temperature.  So you will see an empty temperature graph for this entity.  A fix will require: a) changing HA (to accept a climate entity without a temperature (like a fan entity), or; b) changing the controller to a different entity class (but this may break some of the away mode integrations planned for the future).
2. Away mode (as understood by HA), is not implemented as yet - however, you can use service calls to `climate.set_operation_mode` with the controller or zone entities to set Away mode (as understood by evohome).
3. FIXED (work-around): The underlying api (evohomeclient2) has some issues (e.g. no provision to refresh OAuth tokens, that caused failure after 1 hour).  A proper fix will require changes to evohomeclient2 (possibly causing the existing honeywell component to break).
4. FIXED (architecturally, but still a little messy): The code is currently messy, and architecturally unsatisfying (e.g. the controller updates the zones' private attributes directly).
5. No provision for DHW (yet).  This is in progress.
6. No provision for schedules (yet).  This may shift to v2 of the component.
7. The `scan_interval` parameter is currently ignored, and thus defaults to 120 secs.  This is OK as this code polls Honeywell servers only 3x per scan interval (includes 2 polls for v1 temperatures), or 60 per hour.  This compares to the existing evohome implementation, which is at least once per zone per scan interval.  I understand that up to 250 polls per hour is considered OK.
