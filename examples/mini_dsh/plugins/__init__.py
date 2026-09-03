"""mini_dsh 的 cordis 插件包（组合根）。

每个插件负责一类装配：provide 服务（sessions/llm/tools/skills/compaction）、
注册 agent 监听器（middleware/skill_tool/instructions/compaction）或组合装配
（driver）。加载顺序由 cordis 的 inject 依赖驱动，与 cordis.yml 书写顺序无关。
"""
