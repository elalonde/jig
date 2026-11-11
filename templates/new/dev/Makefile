# The sidecar holds records, not code. Its check is jig trace check, which
# makes sure the trace still ties the record IDs of the specs to the lines of
# the product clone nested here. The recipe is echoed, so the output shows
# that the check ran.
.DEFAULT_GOAL := check
.PHONY: check test

check: test

test:
	jig trace check
