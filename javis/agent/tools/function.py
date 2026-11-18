from __future__ import annotations

import asyncio
import functools
import inspect
import re
from collections.abc import Callable
from types import MethodType
from typing import Any, get_type_hints, overload

from pydantic import BaseModel, create_model

from javis.agent.tools.base import BaseTool, ToolCallResult, ToolExecutionContext

# ============================================================
# 2. FunctionTool：@tool 产生的工具，内部同样是 BaseTool
# ============================================================

class Function(BaseTool):
    def __init__(
        self,
        func: Callable[..., Any],
        name: str,
        description: str,
        args_schema: type[BaseModel] | None,
        single_arg: str | None = None,
        inject_context: bool = False,
        run_in_thread: bool = False,
    ) -> None:
        self.func = func
        self.name = name or str(getattr(func, "__name__", type(func).__name__))
        self.description = description or self._summary(func)
        self.args_schema = args_schema
        # 原函数只有一个参数且为 BaseModel 时，记录参数名
        self._single_arg = single_arg
        self._inject_context = inject_context
        self._is_async = inspect.iscoroutinefunction(func)
        self._run_in_thread = run_in_thread
        functools.update_wrapper(self, func)

    def __get__(self, instance: Any, owner: type[Any] | None = None) -> Function:
        """Bind a tool method to its ToolSet instance."""
        if instance is None:
            return self
        return Function(
            MethodType(self.func, instance),
            self.name,
            self.description,
            self.args_schema,
            self._single_arg,
            self._inject_context,
            self._run_in_thread,
        )

    @staticmethod
    def _summary(func: Callable[..., Any]) -> str:
        doc = inspect.getdoc(func) or ""
        return doc.split("\n", 1)[0].replace("\n", " ").strip()

    async def _run(
        self,
        context: ToolExecutionContext,
        args: BaseModel | None,
    ) -> ToolCallResult:
        """单参数为 BaseModel 时，重新构造模型实例再传给原函数"""
        if self._single_arg is not None:
            if args is None:
                return ToolCallResult(
                    output="工具缺少参数模型",
                    is_error=True,
                )
            call_kwargs: dict[str, Any] = {self._single_arg: args}
        else:
            call_kwargs = {} if args is None else args.model_dump()

        # 按需注入 context（不进入 args_schema）
        if self._inject_context:
            call_kwargs["context"] = context

        # 执行：async 直接 await；sync 可选放线程池
        if self._is_async:
            result = await self.func(**call_kwargs)
        elif self._run_in_thread:
            result = await asyncio.to_thread(self.func, **call_kwargs)
        else:
            result = self.func(**call_kwargs)

        # 统一包装成 ToolCallResult
        if isinstance(result, ToolCallResult):
            return result
        return ToolCallResult(
            output="" if result is None else str(result), is_error=False
        )



# ============================================================
# 3. @tool 装饰器
# ============================================================

_TOOL_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]")


def _clean_tool_name(raw: str) -> str:
    """把非法字符替换成下划线并截断到 64，保证符合 OpenAI 格式要求。"""
    return _TOOL_NAME_RE.sub("_", raw)[:64]


def _wants_context(func: Callable[..., Any]) -> bool:
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return False
    return "context" in sig.parameters


def _infer_args_schema(
    func: Callable[..., Any],
    explicit_schema: type[BaseModel] | None = None,
) -> tuple[type[BaseModel] | None, str | None]:
    """返回 (args_schema, single_arg)。

    single_arg 表示原函数只有一个参数、且该参数是 BaseModel 实例，
    此时 _run 需要把校验后的字段重新打包成模型实例。
    """
    sig = inspect.signature(func)
    hints = get_type_hints(func)

    # 关键：排除 self / cls / context —— context 是运行时注入，不是模型参数
    params = [
        p for n, p in sig.parameters.items()
        if n not in ("self", "cls", "context")
    ]

    if explicit_schema is not None:
        if len(params) == 1:
            return explicit_schema, params[0].name
        return explicit_schema, None

    # 单参数且注解是 BaseModel：直接复用
    if len(params) == 1:
        p = params[0]
        hint = hints.get(p.name, p.annotation)
        if isinstance(hint, type) and issubclass(hint, BaseModel):
            return hint, p.name

    if not params:
        return None, None

    # 从普通类型注解动态构建
    fields: dict[str, Any] = {}
    for p in params:
        hint = hints.get(p.name, p.annotation)
        if hint is inspect.Parameter.empty:
            hint = str
        default = ... if p.default is inspect.Parameter.empty else p.default
        fields[p.name] = (hint, default)

    return create_model(f"{func.__name__}_args", **fields), None


@overload
def tool(func: Callable[..., Any]) -> Function: ...


@overload
def tool(
    func: type[BaseModel],
    *,
    name: str | None = None,
    description: str | None = None,
    args_schema: type[BaseModel] | None = None,
    run_in_thread: bool = False,
) -> Callable[[Callable[..., Any]], Function]: ...


@overload
def tool(
    func: None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    args_schema: type[BaseModel] | None = None,
    run_in_thread: bool = False,
) -> Callable[[Callable[..., Any]], Function]: ...


def tool(
    func: Callable[..., Any] | type[BaseModel] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    args_schema: type[BaseModel] | None = None,
    run_in_thread: bool = False,
) -> Function | Callable[[Callable[..., Any]], Function]:
    """
    用法：
        @tool
        @tool(MyArgs)
        @tool(MyArgs, name="...", description="...")
        @tool(name="...", args_schema=MyArgs)
        @tool(run_in_thread=True)   # 同步阻塞函数放线程池执行
    """
    # @tool(MyArgs) 形式：第一个位置参数是 Pydantic 模型
    if func is not None and isinstance(func, type) and issubclass(func, BaseModel):
        args_schema = func
        func = None

    def decorator(f: Callable[..., Any]) -> Function:
        schema, single_arg = _infer_args_schema(f, args_schema)

        raw_name = name or getattr(f, "__name__", None) or type(f).__name__
        tool_name = _clean_tool_name(raw_name)

        tool_desc = description or (inspect.getdoc(f) or "").strip()
        if not tool_desc:
            raise ValueError(f"{f.__name__} missing docstring, can not generate tool description.")

        return Function(
            f,
            tool_name,
            tool_desc,
            schema,
            single_arg,
            inject_context=_wants_context(f),
            run_in_thread=run_in_thread,
        )

    if func is not None:
        return decorator(func)
    return decorator
