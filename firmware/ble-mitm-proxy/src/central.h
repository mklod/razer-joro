// Last modified: 2026-04-10--0400
#ifndef CENTRAL_H
#define CENTRAL_H

#include <zephyr/bluetooth/conn.h>

/* Start scanning for the real Joro keyboard */
void central_start_scan(void);

/* Discover GATT services on the keyboard after connection */
void central_discover_services(struct bt_conn *conn);

/* Write data to the keyboard's command characteristic (1524) */
int central_write_to_keyboard(const uint8_t *data, uint16_t len);

/* Split write: header then data as separate ATT Write Requests */
int central_write_split(const uint8_t *hdr, uint16_t hdr_len,
			const uint8_t *data, uint16_t data_len);

#endif
