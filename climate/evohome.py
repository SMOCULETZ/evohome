"""
Support for Honeywell (EU-only) Evohome installations: 1 controller & 1+ zones.
"""

import logging

from homeassistant.core import callback  # used for @callback

from custom_components.evohome import (
    evoControllerEntity,
    evoZoneEntity,

    DATA_EVOHOME,
    DISPATCHER_EVOHOME,
)

_LOGGER = logging.getLogger(__name__)


def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up a Honeywell evohome CH/DHW system (1 controller & many zones)."""

    _LOGGER.info("Started: setup_platform()")


# Pull out the domain configuration fromm hass.data
    ec_api = hass.data[DATA_EVOHOME]['evohomeClient']
    ec_loc = hass.data[DATA_EVOHOME]['installation']
#   ec_tmp = hass.data[DATA_EVOHOME]['status']  # not needed during init

    location = ec_loc['locationInfo']
    controller = ec_loc['gateways'][0]['temperatureControlSystems'][0]


# Collect the (master) controller (a merge of location & controller)
#  - controller ID is used in preference to location ID
    _LOGGER.info(
        "Found Controller: id: %s, name: %s, type: %s",
        controller['systemId'],
        location['name'],
        controller['modelType']
    )

    master = evoController(hass, ec_api, controller)  # create the controller

    
    slaves = []
# Collect each (slave) zone as a (climate component) device
    for zone in controller['zones']:
        _LOGGER.info(
            "Found Zone: id: %s, name: %s, type: %s",
            zone['zoneId'],
            zone['name'],
            zone['zoneType']
        )

        
# We may not handle some zones correctly (e.g. UFH) - how to test for them?
#       if zone['zoneType'] in [ "RadiatorZone", "ZoneValves" ]:
        if True:
            slave = evoZone(hass, ec_api, zone)  # create a zone
            slaves.append(slave)  # add this zone to the list of devices


# Collect each (slave) DHW zone as a (climate component) device
    if 'dhw' in controller:
#   if 'modelType' in controller:
        _LOGGER.info(
            "Found DHW: id: %s",
            controller['dhw']['dhwId']
#           controller['modelType']
        )


# Now, add controller and all zones in one batch for efficiency
    add_devices([master] + slaves, False)

    _LOGGER.info("Finished: setup_platform()")
    return True


class evoController(evoControllerEntity):
    """Representation of a Honeywell evohome controller."""

    def __init__(self, hass, client, controller):
        """Initialize the evohome controller."""

        _LOGGER.debug("Started: __init__(Controller = %s)", controller)
        super().__init__(hass, client, controller)

# listen for update packets...
        hass.helpers.dispatcher.async_dispatcher_connect(
            DISPATCHER_EVOHOME,
            self._connect
        )  # for: def async_dispatcher_connect(signal, target)

# Process updates in parallel???
#       parallel_updates = True

        _LOGGER.debug("Finished: __init__(Controller)")
        return


    @callback
    def _connect(self, packet):
        """Process a dispatcher connect."""
        _LOGGER.info(
            "Controller has received a '%s' packet from %s",
            packet['signal'],
            packet['sender']
        )


        self.update
        self.async_schedule_update_ha_state()
        return None
        

    @property
    def should_poll(self):
        """Controller should TBA. The controller will provide the state data."""
        _poll = True
        _LOGGER.info(
            "should_poll(Controller = %s): %s", 
            self._id, 
            _poll
        )
        return _poll


    @property
    def force_update(self):
        """Controllers should update when state date is updated, even if it is unchanged."""
        _force = True
        _LOGGER.info(
            "force_update(Controller = %s): %s", 
            self._id, 
            _force
        )
        return _force


    def set_operation_mode(self, operation_mode):
        _LOGGER.info(
            "Started: set_operation_mode(Controller = %s, op_mode = %s)",
            str(self._id) + " [" + self._name + "]",
            operation_mode
        )
        super().set_operation_mode(self, operation_mode)

#       sleep(10)  # allow system to quiesce...


## Finally, send a message informing the kids that operting mode has changed?...
#       self.hass.bus.fire('mode_changed', {ATTR_ENTITY_ID: self._scs_id, ATTR_STATE: command})
#       refreshEverything()

#       _LOGGER.info("controller.schedule_update_ha_state()")
#       self.schedule_update_ha_state()

        _LOGGER.info("About to send a dispatcher packet...")
        packet = {'sender': 'controller', 'signal': 'update'}
## def async_dispatcher_send(hass, signal, *args):
        self.hass.helpers.dispatcher.async_dispatcher_send(DISPATCHER_EVOHOME, packet)
        return None
        

    def update(self):
        _LOGGER.info(
            "Started: update(Controller = %s)",
            str(self._id) + " [" + self._name + "]"
        )
        super().update()

# ZX: Now (wait 5s and then) send a message to the slaves to update themselves
# store data in hass.data, platforms subscribe with dispatcher_connect, component notifies of updates using dispatch_send
#       sleep(5)
#       _LOGGER.info("About to send UPDATE packet...")
#       self.hass.helpers.dispatcher.async_dispatcher_send(DISPATCHER_EVOHOME, "UPDATE")        ## def async_dispatcher_send(hass, signal, *args):
#       self.hass.helpers.dispatcher.async_dispatcher_connect(DISPATCHER_EVOHOME, self.target)  ## def async_dispatcher_connect(hass, signal, target):
        return None
        

class evoZone(evoZoneEntity):
    """Representation of a Honeywell evohome heating zone."""

    def __init__(self, hass, client, zone):
        """Initialize the evohome zone."""

        _LOGGER.debug("Started: __init__(Zone = %s)", zone)
        super().__init__(hass, client, zone)

# listen for update packets...
        hass.helpers.dispatcher.async_dispatcher_connect(
            DISPATCHER_EVOHOME,
            self._connect
        )  # for: def async_dispatcher_connect(signal, target)




        _LOGGER.debug("Finished: __init__(Zone = %s)", zone)
        return None


    @callback
    def _connect(self, packet):
        """Process a dispatcher connect."""
        _LOGGER.info(
            "Zone %s has received a '%s' packet from %s",
            str(self._id) + " [" + self._name + "]",
            packet['signal'],
            packet['sender']
        )

        self.update
        self.async_schedule_update_ha_state()
        return None


    @property
    def should_poll(self): #   OR: def poll(self):
        """Zones should not be polled?, the controller will maintain state data."""
        _poll = True
        _LOGGER.info(
            "should_poll(Zone = %s): %s", 
            self._id, 
            _poll
        )
        return _poll


    @property
    def force_update(self):
        """Zones should TBA."""
        _force = False
        _LOGGER.info(
            "force_update(Zone = %s): %s", 
            self._id, 
            _force
        )
        return _force


    def update(self):
        _LOGGER.info(
            "Started: update(Zone = %s)",
            str(self._id) + " [" + self._name + "]"
            )
        super().update()
        return None
