---
description: Long document spanning multiple pages with headings, a list and a table.
---
# Incident Postmortem Report

## Summary

On the morning of the incident the primary database cluster experienced a failover that took eleven minutes to complete. During that window roughly four percent of API requests returned server errors.

## Timeline

The first alert fired at nine fourteen when replication lag exceeded the configured threshold. The on call engineer acknowledged the page within two minutes and began investigating the replica health dashboards.

At nine nineteen the primary node stopped accepting writes after its disk filled with write ahead log segments. The automated failover promoted a replica at nine twenty one, but connection pools in the API tier continued pointing at the old primary until they were recycled.

Traffic fully recovered at nine twenty five once all API pods had been restarted by the deployment controller.

## Root Cause

A misconfigured archival job had stopped uploading write ahead log segments to object storage three days earlier. Segments accumulated on local disk until the volume was exhausted.

## Impact

| Metric | Value |
|---|---|
| Duration | 11 minutes |
| Failed requests | 4.1 percent |
| Customers affected | 312 |
| Data loss | None |

## Action Items

- Alert when archival lag exceeds thirty minutes
- Add disk usage alerts at seventy and ninety percent
- Make API connection pools follow the cluster endpoint instead of node addresses
- Run a failover game day every quarter

## Lessons Learned

Silent failures in background jobs are among the most dangerous classes of bugs because they accumulate risk invisibly. Every background job should emit a heartbeat metric that is monitored independently of its success logs.

The team also learned that automated failover is only as fast as the slowest client to notice it. Client side configuration deserves the same review rigor as server side configuration.
