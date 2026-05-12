问题：Wikipedia Current Events 抓取的 summary 会把父级主题、子级链接和来源标记一起拼接，导致摘要不像真正的事件描述。
解决：调整 Wikipedia summary 提取逻辑，优先取含外部证据链接的最内层列表项，并去除主题前缀、嵌套列表文本和末尾来源括号。
