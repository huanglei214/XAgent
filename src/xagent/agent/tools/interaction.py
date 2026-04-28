from __future__ import annotations

import inspect
import json
from typing import Awaitable, Callable, List, Optional, Union

from pydantic import BaseModel, Field, field_validator

from xagent.agent.tools.base import Tool, ToolContext, ToolResult


class AskUserQuestionOption(BaseModel):
    """用户问题的单个选项。"""

    label: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1)
    preview: Optional[str] = None


class AskUserQuestionItem(BaseModel):
    """单个问题条目，包含问题文本及可选选项。"""

    question: str = Field(min_length=1)
    header: str = Field(min_length=1, max_length=12)
    options: List[AskUserQuestionOption] = Field(min_length=2, max_length=4)
    multi_select: bool = False


class AskUserQuestionInput(BaseModel):
    """向用户提问的输入模型，包含 1-4 个问题。"""

    questions: List[AskUserQuestionItem] = Field(min_length=1, max_length=4)


class AskUserQuestionAnswer(BaseModel):
    """单个问题的回答。"""

    question_index: int = Field(ge=0)
    selected_labels: List[str] = Field(min_length=1)


class AskUserQuestionResultData(BaseModel):
    """用户回答结果数据，确保每个问题的索引唯一。"""

    answers: List[AskUserQuestionAnswer]

    @field_validator("answers")
    @classmethod
    def _ensure_unique_question_indexes(cls, answers: List[AskUserQuestionAnswer]) -> List[AskUserQuestionAnswer]:
        indexes = [answer.question_index for answer in answers]
        if len(indexes) != len(set(indexes)):
            raise ValueError("answers contain duplicate question_index values")
        return answers


QuestionCallback = Callable[[AskUserQuestionInput], Union[Awaitable[AskUserQuestionResultData], AskUserQuestionResultData]]


def create_ask_user_question_tool(callback: QuestionCallback) -> Tool:
    """创建一个用于向用户提问的 Tool 实例。"""

    async def _handler(args: AskUserQuestionInput, ctx: ToolContext) -> ToolResult:
        result = callback(args)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, AskUserQuestionResultData):
            data = result
        else:
            data = AskUserQuestionResultData.model_validate(result)
        _validate_answers(args, data)
        return ToolResult.ok(
            f"Collected answers for {len(data.answers)} question(s).",
            content=json.dumps(data.model_dump(mode="json"), ensure_ascii=False),
            data=data.model_dump(mode="json"),
        )

    return Tool(
        name="ask_user_question",
        description=(
            "Ask the user 1-4 structured questions with 2-4 fixed choices each. "
            "Use this when a small number of explicit options would unblock the task."
        ),
        input_model=AskUserQuestionInput,
        handler=_handler,
    )


def _validate_answers(params: AskUserQuestionInput, result: AskUserQuestionResultData) -> None:
    """校验用户回答是否与问题参数一致。"""
    by_index = {answer.question_index: answer for answer in result.answers}
    if len(by_index) != len(params.questions):
        raise ValueError(f"expected {len(params.questions)} answers, got {len(result.answers)}")

    for index, question in enumerate(params.questions):
        answer = by_index.get(index)
        if answer is None:
            raise ValueError(f"missing answer for question {index}")
        labels = {option.label for option in question.options}
        for label in answer.selected_labels:
            if label not in labels:
                raise ValueError(f'unknown label "{label}" for question {index}')
        if question.multi_select:
            if len(answer.selected_labels) < 1:
                raise ValueError(f"question {index} requires at least one selection")
        elif len(answer.selected_labels) != 1:
            raise ValueError(f"question {index} requires exactly one selection")
