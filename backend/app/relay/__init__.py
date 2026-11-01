"""转接模式的邮局：文件信箱 + worker。

平台在这里只做一件事 —— 把 harness 写在 `relay/outbox/` 里的话，按收件人送到。
不建任务、不开工作树、不判内容对错（见 docs/notes/relay-mode.md）。
"""
