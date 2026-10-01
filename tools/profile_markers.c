/* Bound Cachegrind collection to warmed operations, excluding imports. */
#include <valgrind/cachegrind.h>

void profile_start(void) { CACHEGRIND_START_INSTRUMENTATION; }
void profile_stop(void) { CACHEGRIND_STOP_INSTRUMENTATION; }
