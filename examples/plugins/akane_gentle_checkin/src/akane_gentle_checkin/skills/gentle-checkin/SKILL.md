---
name: gentle-checkin
description: Use when the user asks Akane to enable, disable, inspect, or explain a gentle proactive check-in after a quiet QQ conversation.
metadata:
  required_tools: [akane.sample.gentle-checkin.configure.v1]
---

# Gentle Check-in

Use `akane.sample.gentle-checkin.configure.v1` for the current QQ conversation.

- `status` reads the current conversation setting.
- `enable` needs only an idle interval in minutes; the host binds the current conversation.
- `disable` stops future check-ins for the current conversation.
- The check-in fires once for each quiet period. A new inbound message starts a new quiet period.
- In a QQ group, do not guess administrator authority. Explain that an owner/admin should use `/checkin on <minutes>` in that group.
- Report the tool's real status. Do not claim a reminder is active when configuration failed.
