---
name: report
description: How to put together a sales report, such as sales by store for a year
---

# Sales report

1. Settle the period and what counts as a sale. Count completed orders only, unless the user or a memory says otherwise.
2. Call analyze_topics with these topics, each limited to the period:
   - revenue: orders and revenue per store, and the total.
   - returns: returned items and refunds per store.
   - top products: the five products that sold the most units.
3. Write the report as plain text, in this order:
   - one sentence with the period and what was counted;
   - revenue: each store with its orders and revenue, the total and the largest share;
   - returns: the stores with the most and the fewest returns;
   - top products: the first three with their units;
   - one sentence on what stands out.
4. Use the figures of the findings as they are. When a topic has no answer, say which one is missing instead of guessing it.
