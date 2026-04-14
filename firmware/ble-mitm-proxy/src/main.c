// Last modified: 2026-04-10--1710
// BLE MITM Proxy — main entry point
// Effect test: static, breathing 1+2 color, spectrum cycling

#include <zephyr/kernel.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/usb/usb_device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/logging/log.h>

#include <zephyr/settings/settings.h>

#include "central.h"
#include "peripheral.h"
#include "relay.h"

LOG_MODULE_REGISTER(mitm_main, LOG_LEVEL_INF);

static void connected_cb(struct bt_conn *conn, uint8_t err)
{
	char addr[BT_ADDR_LE_STR_LEN];
	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));

	LOG_INF("*** connected_cb: addr=%s err=0x%02x", addr, err);

	if (err) {
		LOG_ERR("Connection failed: %s (err 0x%02x)", addr, err);
		return;
	}

	/* Determine if this is upstream (keyboard) or downstream (PC) */
	struct bt_conn_info info;
	bt_conn_get_info(conn, &info);

	if (info.role == BT_CONN_ROLE_CENTRAL) {
		LOG_INF(">>> UPSTREAM connected: %s (we are central -> keyboard)", addr);
		relay_set_upstream(conn);
		/* Start GATT discovery on the keyboard */
		central_discover_services(conn);
		/* Now start advertising so Synapse can find us */
		peripheral_start_adv();
	} else {
		LOG_INF("<<< DOWNSTREAM connected: %s (Synapse connected to us)", addr);
		relay_set_downstream(conn);
	}
}

static void disconnected_cb(struct bt_conn *conn, uint8_t reason)
{
	char addr[BT_ADDR_LE_STR_LEN];
	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));

	struct bt_conn_info info;
	bt_conn_get_info(conn, &info);

	if (info.role == BT_CONN_ROLE_CENTRAL) {
		LOG_INF(">>> UPSTREAM disconnected: %s (reason 0x%02x)", addr, reason);
		relay_set_upstream(NULL);
		/* Restart scanning for the keyboard */
		central_start_scan();
	} else {
		LOG_INF("<<< DOWNSTREAM disconnected: %s (reason 0x%02x)", addr, reason);
		relay_set_downstream(NULL);
		/* Restart advertising for Synapse */
		peripheral_start_adv();
	}
}

/* --- SMP Pairing Auth Callbacks --- */

static void auth_passkey_display(struct bt_conn *conn, unsigned int passkey)
{
	LOG_INF("SMP: Passkey display: %06u", passkey);
}

static void auth_passkey_confirm(struct bt_conn *conn, unsigned int passkey)
{
	LOG_INF("SMP: Passkey confirm: %06u — auto-accepting", passkey);
	bt_conn_auth_passkey_confirm(conn);
}

static void auth_cancel(struct bt_conn *conn)
{
	LOG_INF("SMP: Pairing cancelled");
}

static void auth_pairing_confirm(struct bt_conn *conn)
{
	LOG_INF("SMP: Pairing confirm requested — accepting");
	bt_conn_auth_pairing_confirm(conn);
}

static void auth_pairing_complete(struct bt_conn *conn, bool bonded)
{
	LOG_INF("SMP: Pairing complete, bonded=%d", bonded);
}

static void auth_pairing_failed(struct bt_conn *conn, enum bt_security_err reason)
{
	LOG_ERR("SMP: Pairing FAILED, reason=%u", reason);
}

static struct bt_conn_auth_cb auth_cb = {
	.passkey_display = auth_passkey_display,
	.passkey_confirm = auth_passkey_confirm,
	.cancel = auth_cancel,
	.pairing_confirm = auth_pairing_confirm,
};

static struct bt_conn_auth_info_cb auth_info_cb = {
	.pairing_complete = auth_pairing_complete,
	.pairing_failed = auth_pairing_failed,
};

/* --- Connection Callbacks --- */

static void security_changed_cb(struct bt_conn *conn, bt_security_t level,
				enum bt_security_err err)
{
	char addr[BT_ADDR_LE_STR_LEN];
	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
	LOG_INF("*** Security changed: %s level=%u err=%u", addr, level, err);
}

BT_CONN_CB_DEFINE(conn_callbacks) = {
	.connected = connected_cb,
	.disconnected = disconnected_cb,
	.security_changed = security_changed_cb,
};

int main(void)
{
	int err;

	/* Enable USB CDC for serial logging */
	err = usb_enable(NULL);
	if (err && err != -EALREADY) {
		LOG_ERR("USB enable failed: %d", err);
	}

	/* Give USB a moment to enumerate */
	k_sleep(K_MSEC(1000));

	LOG_INF("=== Joro BLE MITM Proxy ===");
	LOG_INF("Firmware built " __DATE__ " " __TIME__);

	/* Register SMP auth callbacks for pairing negotiation */
	bt_conn_auth_cb_register(&auth_cb);
	bt_conn_auth_info_cb_register(&auth_info_cb);

	/* Enable Bluetooth */
	err = bt_enable(NULL);
	if (err) {
		LOG_ERR("BT enable failed: %d", err);
		return err;
	}

	/* Load settings (bond info, identity) from flash */
	settings_load();
	LOG_INF("BT enabled, settings loaded");

	/* Start both: advertise for Synapse AND scan for keyboard */
	peripheral_start_adv();
	central_start_scan();

	LOG_INF("Proxy running — advertising + scanning");
	LOG_INF("  Advertising as 'Joro' for Synapse");
	LOG_INF("  Scanning for real Joro keyboard");

	/* Main loop — wait for upstream, then run probe test */
	bool test_done = false;
	while (1) {
		k_sleep(K_SECONDS(2));
		relay_print_stats();

		if (!test_done && relay_upstream_connected()) {
			/* Wait for GATT discovery + notification subscription */
			k_sleep(K_SECONDS(3));
			if (!relay_upstream_connected()) continue;

			LOG_INF("=============================================");
			LOG_INF("=== SET COMMAND TEST — with SMP pairing   ===");
			LOG_INF("=============================================");
			test_done = true;

			static uint8_t txn = 0;

			/* --- PHASE 0: Request BLE encryption --- */
			LOG_INF("--- P0: Requesting BLE encryption ---");
			{
				struct bt_conn *up = relay_get_upstream_conn();
				if (up) {
					int sec_err = bt_conn_set_security(up, BT_SECURITY_L2);
					LOG_INF("Security L2 request: err=%d", sec_err);
					k_sleep(K_SECONDS(3));  /* Give SMP time to negotiate */
				}
			}

			/* --- PHASE 1: Driver init sequence (from HCI capture) --- */
			LOG_INF("--- P1: Driver init sequence ---");

			/* GET 0x01/0xA0 — sent twice by driver on connect */
			uint8_t init1[] = {++txn, 0,0,0, 0x01,0xA0, 0,0};
			central_write_to_keyboard(init1, 8);
			LOG_INF("[%02x] INIT: GET 0x01/0xA0 (1st)", txn);
			k_sleep(K_MSEC(600));

			uint8_t init2[] = {++txn, 0,0,0, 0x01,0xA0, 0,0};
			central_write_to_keyboard(init2, 8);
			LOG_INF("[%02x] INIT: GET 0x01/0xA0 (2nd)", txn);
			k_sleep(K_MSEC(600));

			/* GET 0x05/0x87 sub=00,01 */
			uint8_t init3[] = {++txn, 0,0,0, 0x05,0x87, 0x00,0x01};
			central_write_to_keyboard(init3, 8);
			LOG_INF("[%02x] INIT: GET 0x05/0x87 sub=00,01", txn);
			k_sleep(K_MSEC(600));

			/* GET 0x05/0x84 */
			uint8_t init4[] = {++txn, 0,0,0, 0x05,0x84, 0,0};
			central_write_to_keyboard(init4, 8);
			LOG_INF("[%02x] INIT: GET 0x05/0x84", txn);
			k_sleep(K_MSEC(600));

			/* SET 0x05/0x07 sub=00,01 data=[00] — driver config (SPLIT write) */
			{
				uint8_t hdr[] = {++txn, 0x01,0,0, 0x05,0x07, 0x00,0x01};
				uint8_t dat[] = {0x00};
				central_write_split(hdr, 8, dat, 1);
				LOG_INF("[%02x] INIT: SET 0x05/0x07 sub=00,01 d=[00] (split)", txn);
			}
			k_sleep(K_MSEC(600));

			/* --- PHASE 2: GET current state --- */
			LOG_INF("--- P2: Read current state ---");

			uint8_t g_brt[] = {++txn, 0,0,0, 0x10,0x85, 0,0};
			central_write_to_keyboard(g_brt, 8);
			LOG_INF("[%02x] GET brightness 0x10/0x85", txn);
			k_sleep(K_MSEC(600));

			uint8_t g_light[] = {++txn, 0,0,0, 0x10,0x83, 0,0};
			central_write_to_keyboard(g_light, 8);
			LOG_INF("[%02x] GET lighting 0x10/0x83", txn);
			k_sleep(K_MSEC(600));

			/* --- PHASE 3: Brightness MAX --- */
			LOG_INF("--- P3: Brightness MAX ---");
			{
				uint8_t hdr[] = {++txn, 0x01,0,0, 0x10,0x05, 0x01,0x00};
				uint8_t dat[] = {0xFF};
				central_write_split(hdr, 8, dat, 1);
				LOG_INF("[%02x] SET BRT MAX", txn);
			}
			k_sleep(K_SECONDS(1));

			/* --- PHASE 4: EFFECTS with correct variable-length data --- */
			LOG_INF("--- P4: EFFECT TEST (correct formats from HCI capture) ---");
			LOG_INF(">>> WATCH THE KEYBOARD <<<");

			/* 1. Static RED (dlen=7) */
			{
				uint8_t hdr[] = {++txn, 0x07,0,0, 0x10,0x03, 0x01,0x00};
				uint8_t dat[] = {0x01, 0x00, 0x00, 0x01, 0xFF, 0x00, 0x00};
				central_write_split(hdr, 8, dat, 7);
				LOG_INF("[%02x] STATIC RED", txn);
			}
			k_sleep(K_SECONDS(4));

			/* 2. Breathing BLUE — 1 color (dlen=7) */
			{
				uint8_t hdr[] = {++txn, 0x07,0,0, 0x10,0x03, 0x01,0x00};
				uint8_t dat[] = {0x02, 0x01, 0x00, 0x01, 0x00, 0x00, 0xFF};
				central_write_split(hdr, 8, dat, 7);
				LOG_INF("[%02x] BREATHING BLUE (1 color)", txn);
			}
			k_sleep(K_SECONDS(8));

			/* 3. Breathing RED+GREEN — 2 colors (dlen=10) */
			{
				uint8_t hdr[] = {++txn, 0x0A,0,0, 0x10,0x03, 0x01,0x00};
				uint8_t dat[] = {0x02, 0x02, 0x00, 0x02,
					0xFF, 0x00, 0x00,   /* color 1: red */
					0x00, 0xFF, 0x00};   /* color 2: green */
				central_write_split(hdr, 8, dat, 10);
				LOG_INF("[%02x] BREATHING RED+GREEN (2 color)", txn);
			}
			k_sleep(K_SECONDS(8));

			/* 4. Spectrum cycling (dlen=4) */
			{
				uint8_t hdr[] = {++txn, 0x04,0,0, 0x10,0x03, 0x01,0x00};
				uint8_t dat[] = {0x03, 0x00, 0x00, 0x00};
				central_write_split(hdr, 8, dat, 4);
				LOG_INF("[%02x] SPECTRUM CYCLING", txn);
			}
			k_sleep(K_SECONDS(8));

			/* 5. Static GREEN (back to simple) */
			{
				uint8_t hdr[] = {++txn, 0x07,0,0, 0x10,0x03, 0x01,0x00};
				uint8_t dat[] = {0x01, 0x00, 0x00, 0x01, 0x00, 0xFF, 0x00};
				central_write_split(hdr, 8, dat, 7);
				LOG_INF("[%02x] STATIC GREEN", txn);
			}
			k_sleep(K_SECONDS(3));

			/* 6. Static PURPLE (final) */
			{
				uint8_t hdr[] = {++txn, 0x07,0,0, 0x10,0x03, 0x01,0x00};
				uint8_t dat[] = {0x01, 0x00, 0x00, 0x01, 0x80, 0x00, 0xFF};
				central_write_split(hdr, 8, dat, 7);
				LOG_INF("[%02x] STATIC PURPLE", txn);
			}
			k_sleep(K_SECONDS(3));

			/* Read final state */
			uint8_t g_light2[] = {++txn, 0,0,0, 0x10,0x83, 0,0};
			central_write_to_keyboard(g_light2, 8);
			LOG_INF("[%02x] GET final lighting state", txn);
			k_sleep(K_MSEC(600));

			LOG_INF("=============================================");
			LOG_INF("=== SET COMMAND TEST COMPLETE             ===");
			LOG_INF("=============================================");
		}
	}

	return 0;
}
