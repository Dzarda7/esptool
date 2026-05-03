cmake_minimum_required(VERSION 3.16)

if(WIN32)
    set(SERIAL_PLATFORM_SRC platform/win/serial.c)
    set(SERIAL_PLATFORM_LIBS advapi32)
else()
    set(SERIAL_PLATFORM_SRC platform/posix/serial.c)
    set(SERIAL_PLATFORM_LIBS "")
endif()

add_library(esf_port STATIC
    esf_port.c
    ${SERIAL_PLATFORM_SRC}
)

target_include_directories(esf_port
    PUBLIC  ${CMAKE_CURRENT_SOURCE_DIR}
    PRIVATE ${ESP_SERIAL_FLASHER_INCLUDE_DIR}
)

target_link_libraries(esf_port
    PRIVATE ${SERIAL_PLATFORM_LIBS}
)
