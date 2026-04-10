# Last modified: 2026-04-10--0400
cmake_minimum_required(VERSION 3.20.0)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(ble_mitm_proxy)

target_sources(app PRIVATE
  src/main.c
  src/central.c
  src/peripheral.c
  src/relay.c
)
