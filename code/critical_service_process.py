# Implement the core service as an independent running process. The process must
# experience unpredictable crashes during normal execution.
# Rule: Do not hard-code an arbitrary static conditional (e.g., if (request_count == 5)
# exit()). Implement stochastic or environmental crash triggers, such as:
#  A random probability trigger on incoming requests (e.g., simulating a memory
# leak or segfault on malformed input).
#  A randomized background fault-injection thread.
# The failure must terminate/exit the process completely (do not simply put the process to sleep). 