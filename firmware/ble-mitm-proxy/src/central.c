// Last modified: 2026-04-10--1700
// Central role — scan for and connect to the real Joro keyboard,
// discover GATT services, subscribe to notifications

#include <zephyr/kernel.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/logging/log.h>

#include "central.h"
#include "relay.h"

LOG_MODULE_REGISTER(central, LOG_LEVEL_INF);

/* Razer custom service UUID: 52401523-f97c-7f90-0e7f-6c6f4e36db1c */
static struct bt_uuid_128 razer_svc_uuid = BT_UUID_INIT_128(
	BT_UUID_128_ENCODE(0x52401523, 0xf97c, 0x7f90, 0x0e7f, 0x6c6f4e36db1c));

/* Characteristic UUIDs */
static struct bt_uuid_128 char_1524_uuid = BT_UUID_INIT_128(
	BT_UUID_128_ENCODE(0x52401524, 0xf97c, 0x7f90, 0x0e7f, 0x6c6f4e36db1c));
static struct bt_uuid_128 char_1525_uuid = BT_UUID_INIT_128(
	BT_UUID_128_ENCODE(0x52401525, 0xf97c, 0x7f90, 0x0e7f, 0x6c6f4e36db1c));
static struct bt_uuid_128 char_1526_uuid = BT_UUID_INIT_128(
	BT_UUID_128_ENCODE(0x52401526, 0xf97c, 0x7f90, 0x0e7f, 0x6c6f4e36db1c));

/* Discovered handle for the write characteristic (1524) */
static uint16_t write_handle;

/* Connection to the real keyboard */
static struct bt_conn *kb_conn;

/* Subscription state */
static struct bt_gatt_subscribe_params sub_1525;
static struct bt_gatt_subscribe_params sub_1526;

/* Discovery state */
static struct bt_gatt_discover_params discover_params;
static enum {
	DISC_STATE_SVC,
	DISC_STATE_CHAR,
	DISC_STATE_DONE,
} disc_state;

static uint8_t connect_attempts;
#define MAX_CONNECT_ATTEMPTS 10

/* Forward declarations */
static uint8_t discover_cb(struct bt_conn *conn,
			    const struct bt_gatt_attr *attr,
			    struct bt_gatt_discover_params *params);

/* --- Notification callbacks --- */

static uint8_t notify_1525_cb(struct bt_conn *conn,
			      struct bt_gatt_subscribe_params *params,
			      const void *data, uint16_t length)
{
	if (!data) {
		LOG_INF("1525 subscription ended");
		return BT_GATT_ITER_STOP;
	}
	relay_keyboard_notify_1525(data, length);
	return BT_GATT_ITER_CONTINUE;
}

static uint8_t notify_1526_cb(struct bt_conn *conn,
			      struct bt_gatt_subscribe_params *params,
			      const void *data, uint16_t length)
{
	if (!data) {
		LOG_INF("1526 subscription ended");
		return BT_GATT_ITER_STOP;
	}
	relay_keyboard_notify_1526(data, length);
	return BT_GATT_ITER_CONTINUE;
}

/* --- GATT Discovery --- */

static uint16_t svc_start_handle;
static uint16_t svc_end_handle;

static void subscribe_to_char(struct bt_conn *conn, uint16_t value_handle,
			      struct bt_gatt_subscribe_params *sub,
			      bt_gatt_notify_func_t cb, const char *name)
{
	sub->notify = cb;
	sub->value_handle = value_handle;
	/* CCC handle is typically value_handle + 1 */
	sub->ccc_handle = value_handle + 1;
	sub->value = BT_GATT_CCC_NOTIFY;
	atomic_set_bit(sub->flags, BT_GATT_SUBSCRIBE_FLAG_VOLATILE);

	int err = bt_gatt_subscribe(conn, sub);
	if (err && err != -EALREADY) {
		LOG_ERR("Subscribe %s failed: %d", name, err);
	} else {
		LOG_INF("Subscribed to %s (handle %u)", name, value_handle);
	}
}

static uint8_t discover_cb(struct bt_conn *conn,
			    const struct bt_gatt_attr *attr,
			    struct bt_gatt_discover_params *params)
{
	if (!attr) {
		if (disc_state == DISC_STATE_SVC) {
			LOG_ERR("Razer custom service not found on keyboard!");
			return BT_GATT_ITER_STOP;
		}
		/* Characteristic discovery complete */
		LOG_INF("GATT discovery complete. Write handle=%u", write_handle);
		disc_state = DISC_STATE_DONE;
		return BT_GATT_ITER_STOP;
	}

	if (disc_state == DISC_STATE_SVC) {
		/* Found the service — record handle range */
		struct bt_gatt_service_val *svc = attr->user_data;
		svc_start_handle = attr->handle;
		svc_end_handle = svc->end_handle;
		LOG_INF("Found Razer service: handles %u-%u",
			svc_start_handle, svc_end_handle);

		/* Now discover characteristics within this service */
		disc_state = DISC_STATE_CHAR;
		discover_params.uuid = NULL; /* all characteristics */
		discover_params.start_handle = svc_start_handle + 1;
		discover_params.end_handle = svc_end_handle;
		discover_params.type = BT_GATT_DISCOVER_CHARACTERISTIC;
		discover_params.func = discover_cb;

		int err = bt_gatt_discover(conn, &discover_params);
		if (err) {
			LOG_ERR("Char discovery failed: %d", err);
		}
		return BT_GATT_ITER_STOP;
	}

	if (disc_state == DISC_STATE_CHAR) {
		struct bt_gatt_chrc *chrc = attr->user_data;
		char uuid_str[BT_UUID_STR_LEN];
		bt_uuid_to_str(chrc->uuid, uuid_str, sizeof(uuid_str));
		LOG_INF("  Char: %s handle=%u props=0x%02x",
			uuid_str, chrc->value_handle, chrc->properties);

		if (!bt_uuid_cmp(chrc->uuid, &char_1524_uuid.uuid)) {
			write_handle = chrc->value_handle;
			LOG_INF("  -> 1524 (write/command) handle=%u", write_handle);
		} else if (!bt_uuid_cmp(chrc->uuid, &char_1525_uuid.uuid)) {
			LOG_INF("  -> 1525 (notify/response) handle=%u", chrc->value_handle);
			subscribe_to_char(conn, chrc->value_handle,
					  &sub_1525, notify_1525_cb, "1525");
		} else if (!bt_uuid_cmp(chrc->uuid, &char_1526_uuid.uuid)) {
			LOG_INF("  -> 1526 (notify/secondary) handle=%u", chrc->value_handle);
			subscribe_to_char(conn, chrc->value_handle,
					  &sub_1526, notify_1526_cb, "1526");
		}
		return BT_GATT_ITER_CONTINUE;
	}

	return BT_GATT_ITER_STOP;
}

void central_discover_services(struct bt_conn *conn)
{
	LOG_INF("Starting GATT discovery on keyboard...");

	kb_conn = conn;
	disc_state = DISC_STATE_SVC;
	write_handle = 0;

	discover_params.uuid = &razer_svc_uuid.uuid;
	discover_params.start_handle = BT_ATT_FIRST_ATTRIBUTE_HANDLE;
	discover_params.end_handle = BT_ATT_LAST_ATTRIBUTE_HANDLE;
	discover_params.type = BT_GATT_DISCOVER_PRIMARY;
	discover_params.func = discover_cb;

	int err = bt_gatt_discover(conn, &discover_params);
	if (err) {
		LOG_ERR("Service discovery failed: %d", err);
	}
}

/* --- Write to keyboard --- */

/* Write completion callback for bt_gatt_write */
static volatile bool write_done;
static volatile int write_err;

static void write_cb(struct bt_conn *conn, uint8_t err,
		     struct bt_gatt_write_params *params)
{
	write_err = err;
	write_done = true;
	if (err) {
		LOG_ERR("GATT write failed: err=%u", err);
	}
}

static struct bt_gatt_write_params write_params;

int central_write_to_keyboard(const uint8_t *data, uint16_t len)
{
	if (!kb_conn || !write_handle) {
		return -ENOTCONN;
	}

	/* Use ATT Write Request (opcode 0x12), NOT Write Without Response.
	 * The keyboard's char 1524 has props=0x08 (Write only, no WwoR).
	 * The Razer driver uses Write Request — keyboard may reject WwoR for SET cmds. */
	write_params.handle = write_handle;
	write_params.offset = 0;
	write_params.data = data;
	write_params.length = len;
	write_params.func = write_cb;
	write_done = false;

	int err = bt_gatt_write(kb_conn, &write_params);
	if (err) {
		return err;
	}

	/* Wait for write completion (with timeout) */
	for (int i = 0; i < 50 && !write_done; i++) {
		k_sleep(K_MSEC(10));
	}
	return write_done ? write_err : -ETIMEDOUT;
}

int central_write_split(const uint8_t *hdr, uint16_t hdr_len,
			const uint8_t *data, uint16_t data_len)
{
	/* Send Protocol30 command as SPLIT writes (like Razer driver):
	 *   Write 1: 8-byte header
	 *   Write 2: data payload (separate ATT Write Request)
	 * This is required for SET commands — concatenated writes return FAILURE. */
	int err;

	/* Write header */
	err = central_write_to_keyboard(hdr, hdr_len);
	if (err) {
		LOG_ERR("Split write: header failed: %d", err);
		return err;
	}

	if (data_len > 0 && data != NULL) {
		/* Small delay between writes (driver had ~100ms gap) */
		k_sleep(K_MSEC(50));

		/* Write data payload */
		err = central_write_to_keyboard(data, data_len);
		if (err) {
			LOG_ERR("Split write: data failed: %d", err);
			return err;
		}
	}
	return 0;
}

/* --- Scanning --- */

static bool is_joro_device(struct bt_data *data, void *user_data)
{
	bool *found = user_data;

	if (data->type == BT_DATA_NAME_COMPLETE ||
	    data->type == BT_DATA_NAME_SHORTENED) {
		if (data->data_len >= 4 &&
		    memcmp(data->data, "Joro", 4) == 0) {
			*found = true;
			return false; /* stop parsing */
		}
	}
	return true; /* continue */
}

static void scan_cb(const bt_addr_le_t *addr, int8_t rssi,
		    uint8_t type, struct net_buf_simple *ad)
{
	bool found = false;
	char addr_str[BT_ADDR_LE_STR_LEN];

	bt_data_parse(ad, is_joro_device, &found);
	if (!found) {
		return;
	}

	bt_addr_le_to_str(addr, addr_str, sizeof(addr_str));

	if (connect_attempts >= MAX_CONNECT_ATTEMPTS) {
		/* Stop hammering — log and wait for manual reset */
		return;
	}

	LOG_INF("Found Joro: %s (RSSI %d, type=%u, attempt %u/%u)",
		addr_str, rssi, type, connect_attempts + 1, MAX_CONNECT_ATTEMPTS);

	/* Only connect to connectable advertisements */
	if (type != BT_GAP_ADV_TYPE_ADV_IND &&
	    type != BT_GAP_ADV_TYPE_ADV_DIRECT_IND) {
		LOG_WRN("  Not connectable (type=%u), skipping", type);
		return;
	}

	connect_attempts++;

	/* Stop scanning and advertising before connecting —
	 * simultaneous adv+connect can cause radio conflicts */
	bt_le_adv_stop();
	int err = bt_le_scan_stop();
	if (err) {
		LOG_ERR("Scan stop failed: %d", err);
		return;
	}

	/* Connect to the keyboard — use longer interval and supervision timeout
	 * to be more tolerant of the link-layer handshake */
	struct bt_conn *conn = NULL;
	static struct bt_le_conn_param conn_param = {
		.interval_min = 24,   /* 30ms */
		.interval_max = 40,   /* 50ms */
		.latency = 0,
		.timeout = 400,       /* 4.0s — very generous supervision timeout */
	};
	struct bt_le_conn_param *param = &conn_param;

	err = bt_conn_le_create(addr, BT_CONN_LE_CREATE_CONN, param, &conn);
	if (err) {
		LOG_ERR("Connect to keyboard failed: %d", err);
		/* Restart scanning */
		central_start_scan();
		return;
	}

	/* Connection ref will be handled in connected callback */
	bt_conn_unref(conn);
}

void central_start_scan(void)
{
	struct bt_le_scan_param scan_param = {
		.type = BT_LE_SCAN_TYPE_PASSIVE,
		.options = BT_LE_SCAN_OPT_NONE,
		.interval = BT_GAP_SCAN_FAST_INTERVAL,
		.window = BT_GAP_SCAN_FAST_WINDOW,
	};

	int err = bt_le_scan_start(&scan_param, scan_cb);
	if (err) {
		LOG_ERR("Scan start failed: %d", err);
	} else {
		LOG_INF("Scanning for Joro keyboard...");
	}
}
