// Last modified: 2026-04-10--0400
// Peripheral role — advertise as "Joro", mirror the Razer custom GATT service,
// receive writes from Synapse and forward to relay

#include <zephyr/kernel.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/logging/log.h>

#include "peripheral.h"
#include "relay.h"

LOG_MODULE_REGISTER(peripheral, LOG_LEVEL_INF);

/* Razer custom service UUID: 52401523-f97c-7f90-0e7f-6c6f4e36db1c */
#define RAZER_SVC_UUID \
	BT_UUID_128_ENCODE(0x52401523, 0xf97c, 0x7f90, 0x0e7f, 0x6c6f4e36db1c)
#define RAZER_1524_UUID \
	BT_UUID_128_ENCODE(0x52401524, 0xf97c, 0x7f90, 0x0e7f, 0x6c6f4e36db1c)
#define RAZER_1525_UUID \
	BT_UUID_128_ENCODE(0x52401525, 0xf97c, 0x7f90, 0x0e7f, 0x6c6f4e36db1c)
#define RAZER_1526_UUID \
	BT_UUID_128_ENCODE(0x52401526, 0xf97c, 0x7f90, 0x0e7f, 0x6c6f4e36db1c)

static struct bt_uuid_128 svc_uuid = BT_UUID_INIT_128(RAZER_SVC_UUID);
static struct bt_uuid_128 char_1524 = BT_UUID_INIT_128(RAZER_1524_UUID);
static struct bt_uuid_128 char_1525 = BT_UUID_INIT_128(RAZER_1525_UUID);
static struct bt_uuid_128 char_1526 = BT_UUID_INIT_128(RAZER_1526_UUID);

/* Buffers for readable characteristic values */
static uint8_t val_1525[20];
static uint8_t val_1526[8];

/* --- Characteristic callbacks --- */

/* 1524: Write from PC (command channel) */
static ssize_t write_1524_cb(struct bt_conn *conn,
			     const struct bt_gatt_attr *attr,
			     const void *buf, uint16_t len,
			     uint16_t offset, uint8_t flags)
{
	LOG_INF("PC wrote to 1524: %u bytes", len);
	relay_pc_write(buf, len);
	return len;
}

/* 1525: Read from PC */
static ssize_t read_1525_cb(struct bt_conn *conn,
			    const struct bt_gatt_attr *attr,
			    void *buf, uint16_t len,
			    uint16_t offset)
{
	return bt_gatt_attr_read(conn, attr, buf, len, offset,
				 val_1525, sizeof(val_1525));
}

/* 1526: Read from PC */
static ssize_t read_1526_cb(struct bt_conn *conn,
			    const struct bt_gatt_attr *attr,
			    void *buf, uint16_t len,
			    uint16_t offset)
{
	return bt_gatt_attr_read(conn, attr, buf, len, offset,
				 val_1526, sizeof(val_1526));
}

/* --- GATT Service Definition ---
 * Mirror the real Joro's Razer custom service exactly:
 *   1524 — write (command input)
 *   1525 — read + notify (response, 20 bytes)
 *   1526 — read + notify (secondary, 8 bytes)
 */
BT_GATT_SERVICE_DEFINE(razer_svc,
	BT_GATT_PRIMARY_SERVICE(&svc_uuid),

	/* 1524: write characteristic */
	BT_GATT_CHARACTERISTIC(&char_1524.uuid,
		BT_GATT_CHRC_WRITE | BT_GATT_CHRC_WRITE_WITHOUT_RESP,
		BT_GATT_PERM_WRITE,
		NULL, write_1524_cb, NULL),

	/* 1525: read + notify characteristic */
	BT_GATT_CHARACTERISTIC(&char_1525.uuid,
		BT_GATT_CHRC_READ | BT_GATT_CHRC_NOTIFY,
		BT_GATT_PERM_READ,
		read_1525_cb, NULL, NULL),
	BT_GATT_CCC(NULL, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),

	/* 1526: read + notify characteristic */
	BT_GATT_CHARACTERISTIC(&char_1526.uuid,
		BT_GATT_CHRC_READ | BT_GATT_CHRC_NOTIFY,
		BT_GATT_PERM_READ,
		read_1526_cb, NULL, NULL),
	BT_GATT_CCC(NULL, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),
);

/* --- Advertising --- */

static const struct bt_data ad[] = {
	BT_DATA_BYTES(BT_DATA_FLAGS,
		      BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR),
	BT_DATA(BT_DATA_NAME_COMPLETE, "Joro", 4),
};

static const struct bt_data sd[] = {
	BT_DATA_BYTES(BT_DATA_UUID128_ALL, RAZER_SVC_UUID),
};

void peripheral_start_adv(void)
{
	int err = bt_le_adv_start(BT_LE_ADV_CONN_FAST_2, ad, ARRAY_SIZE(ad),
				  sd, ARRAY_SIZE(sd));
	if (err) {
		LOG_ERR("Advertising start failed: %d", err);
	} else {
		LOG_INF("Advertising as 'Joro'");
	}
}

/* --- Notify downstream (PC) --- */

int peripheral_notify_1525(const uint8_t *data, uint16_t len)
{
	/* Update stored value */
	uint16_t copy_len = MIN(len, sizeof(val_1525));
	memcpy(val_1525, data, copy_len);

	/* Find the 1525 attribute — it's the 4th in the service definition
	 * (service, 1524 decl, 1524 val, 1525 decl, 1525 val) */
	const struct bt_gatt_attr *attr =
		&razer_svc.attrs[4]; /* 1525 value attr */

	return bt_gatt_notify(NULL, attr, data, len);
}

int peripheral_notify_1526(const uint8_t *data, uint16_t len)
{
	uint16_t copy_len = MIN(len, sizeof(val_1526));
	memcpy(val_1526, data, copy_len);

	/* 1526 value attr: after 1525's CCC descriptor */
	const struct bt_gatt_attr *attr =
		&razer_svc.attrs[7]; /* 1526 value attr */

	return bt_gatt_notify(NULL, attr, data, len);
}
