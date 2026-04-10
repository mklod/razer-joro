// Last modified: 2026-04-10--0400
#ifndef RELAY_H
#define RELAY_H

#include <zephyr/bluetooth/conn.h>

/* Set/clear connection references */
void relay_set_upstream(struct bt_conn *conn);
void relay_set_downstream(struct bt_conn *conn);

/* Called when PC writes to our peripheral (forward to keyboard) */
void relay_pc_write(const uint8_t *data, uint16_t len);

/* Called when keyboard sends notification on 1525 (forward to PC) */
void relay_keyboard_notify_1525(const uint8_t *data, uint16_t len);

/* Called when keyboard sends notification on 1526 (forward to PC) */
void relay_keyboard_notify_1526(const uint8_t *data, uint16_t len);

/* Print relay statistics */
void relay_print_stats(void);

/* Check if upstream (keyboard) is connected */
bool relay_upstream_connected(void);

/* Get upstream connection reference (for security requests etc.) */
struct bt_conn *relay_get_upstream_conn(void);

#endif
