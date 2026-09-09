---
name: <케밥-케이스. 파일 이름과 같게. Agent 호출의 subagent_type>
description: <무엇을 맡는지, 어느 스킬의 어느 단계에서 호출되는지>
model: opus
# tools 를 빼면 전부 쓸 수 있다. 검토 전용이면 Read, Grep, Glob 정도로 좁힌다
# tools: Read, Grep, Glob, Bash, Edit, Write, Agent, SendMessage
---

# <역할 이름>

<!-- 한 단락. 이 에이전트가 하는 일과 하지 않는 일 -->

## 먼저 읽는 것

1.
2.

## 작업 원칙

**<원칙 한 줄>.** 왜 그런지 한두 문장.

## 산출물

<!-- 어디에 무엇을 남기는지. 예: `_workspace/03_impl.md` -->

## 끝내는 조건

<!-- 무엇이 확인되면 끝인지. 미결정 값을 만나면 만들지 말고 보고 -->
