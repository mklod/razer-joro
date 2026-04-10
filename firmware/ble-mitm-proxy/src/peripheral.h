// Last modified: 2026-04-10--0400
#ifndef PERIPHERAL_H
#define PERIPHERAL_H

/* Start advertising as "Joro" */
void peripheral_start_adv(void);

/* Send notification to downstream (PC/Synapse) on char 1525 */
int peripheral_notify_1525(const uint8_t *data, uint16_t len);

/* Send notification to downstream (PC/Synapse) on char 1526 */
int peripheral_notify_1526(const uint8_t *data, uint16_t len);

#endif
