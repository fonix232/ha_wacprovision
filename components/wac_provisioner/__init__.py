"""ESPHome external component: WAC radio for the wac_provision HA integration.

Passively reports unconfigured HomeKit (WAC) accessories it hears, and provisions one on
request. See README for the YAML that wires the events and the API action.
"""

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome import automation
from esphome.components import logger, wifi
from esphome.const import CONF_ID, CONF_TRIGGER_ID

CODEOWNERS = ["@fonix232"]
DEPENDENCIES = ["wifi", "api", "logger"]

CONF_SCAN_INTERVAL = "scan_interval"
CONF_DEADLINE = "deadline"
CONF_LOG_CACHE_SIZE = "log_cache_size"
CONF_ON_FOUND = "on_found"
CONF_ON_RESULT = "on_result"

wac_ns = cg.esphome_ns.namespace("wac_provisioner")
WacProvisioner = wac_ns.class_("WacProvisioner", cg.Component)
WacAccessory = wac_ns.struct("WacAccessory")
WacResult = wac_ns.struct("WacResult")
FoundTrigger = wac_ns.class_("FoundTrigger", automation.Trigger.template(WacAccessory.operator("const").operator("ref")))
ResultTrigger = wac_ns.class_("ResultTrigger", automation.Trigger.template(WacResult.operator("const").operator("ref")))

CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(WacProvisioner),
            cv.Optional(CONF_SCAN_INTERVAL, default="5min"): cv.positive_time_period_milliseconds,
            cv.Optional(CONF_DEADLINE, default="45s"): cv.All(
                cv.positive_time_period_milliseconds,
                cv.Range(min=cv.TimePeriod(seconds=15), max=cv.TimePeriod(seconds=120)),
            ),
            cv.Optional(CONF_LOG_CACHE_SIZE, default=4096): cv.int_range(min=512, max=65536),
            cv.Optional(CONF_ON_FOUND): automation.validate_automation(
                {cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(FoundTrigger)}
            ),
            cv.Optional(CONF_ON_RESULT): automation.validate_automation(
                {cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(ResultTrigger)}
            ),
        }
    ).extend(cv.COMPONENT_SCHEMA),
    cv.only_on_esp32,
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    # SSID and channel of WAC soft-APs come from the wifi component's scan results, so it
    # must keep all of them (not just configured networks) and tell us after each scan.
    wifi.request_wifi_scan_results()
    wifi.request_wifi_scan_results_listener()
    logger.request_log_listener()
    cg.add(var.set_scan_interval(config[CONF_SCAN_INTERVAL]))
    cg.add(var.set_deadline(config[CONF_DEADLINE]))
    cg.add(var.set_log_cache_size(config[CONF_LOG_CACHE_SIZE]))
    for conf in config.get(CONF_ON_FOUND, []):
        trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID], var)
        await automation.build_automation(trigger, [(WacAccessory.operator("const").operator("ref"), "acc")], conf)
    for conf in config.get(CONF_ON_RESULT, []):
        trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID], var)
        await automation.build_automation(trigger, [(WacResult.operator("const").operator("ref"), "res")], conf)
