// Last modified: 2026-04-10--1650
// Relay layer — forwards traffic between upstream (keyboard) and downstream (PC)
// Logs all GATT operations in hex for protocol analysis

#include <zephyr/kernel.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/logging/log.h>

#include "relay.h"
#include "central.h"
#include "peripheral.h"

LOG_MODULE_REGISTER(relay, LOG_LEVEL_INF);

static struct bt_conn *upstream_conn;   /* keyboard */
static struct bt_conn *downstream_conn; /* PC/Synapse */
static uint32_t pc_writes;
static uint32_t kb_notif_1525;
static uint32_t kb_notif_1526;

static void log_hex(const char *prefix, const uint8_t *data, uint16_t len)
{
	/* Print hex dump for protocol analysis */
	printk("%s [%u bytes]:", prefix, len);
	for (uint16_t i = 0; i < len; i++) {
		printk(" %02x", data[i]);
	}
	printk("\n");
}

void relay_set_upstream(struct bt_conn *conn)
{
	if (upstream_conn) {
		bt_conn_unref(upstream_conn);
	}
	upstream_conn = conn ? bt_conn_ref(conn) : NULL;
}

void relay_set_downstream(struct bt_conn *conn)
{
	if (downstream_conn) {
		bt_conn_unref(downstream_conn);
	}
	downstream_conn = conn ? bt_conn_ref(conn) : NULL;
}

void relay_pc_write(const uint8_t *data, uint16_t len)
{
	pc_writes++;
	log_hex("PC->KB WRITE", data, len);

	if (!upstream_conn) {
		LOG_WRN("PC->KB: no upstream connection, dropping");
		return;
	}

	int err = central_write_to_keyboard(data, len);
	if (err) {
		LOG_ERR("PC->KB: write failed: %d", err);
	}
}

void relay_keyboard_notify_1525(const uint8_t *data, uint16_t len)
{
	kb_notif_1525++;
	log_hex("KB->PC NOTIFY 1525", data, len);

	if (!downstream_conn) {
		LOG_WRN("KB->PC 1525: no downstream connection, dropping");
		return;
	}

	int err = peripheral_notify_1525(data, len);
	if (err) {
		LOG_ERR("KB->PC 1525: notify failed: %d", err);
	}
}

void relay_keyboard_notify_1526(const uint8_t *data, uint16_t len)
{
	kb_notif_1526++;
	log_hex("KB->PC NOTIFY 1526", data, len);

	if (!downstream_conn) {
		LOG_WRN("KB->PC 1526: no downstream connection, dropping");
		return;
	}

	int err = peripheral_notify_1526(data, len);
	if (err) {
		LOG_ERR("KB->PC 1526: notify failed: %d", err);
	}
}

bool relay_upstream_connected(void)
{
	return upstream_conn != NULL;
}

struct bt_conn *relay_get_upstream_conn(void)
{
	return upstream_conn;
}

void relay_print_stats(void)
{
	LOG_INF("Stats: PC->KB writes=%u, KB->PC 1525=%u, KB->PC 1526=%u, "
		"upstream=%s, downstream=%s",
		pc_writes, kb_notif_1525, kb_notif_1526,
		upstream_conn ? "connected" : "none",
		downstream_conn ? "connected" : "none");
}
