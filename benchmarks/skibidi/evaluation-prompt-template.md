You are grading whether an AI model understands a current internet slang term well enough to explain it to a user.

Focus on practical correctness. The response does not need to use the exact wording below, but it must be satisfactory enough that the user walks away with the core meaning of the term.

The user asked:
{question}

The term being tested is:
{term}

Expected satisfactory answer:
{expected_answer}

The model responded:
{response}

Grade the response using this binary rubric:

- Score 0: The response is not satisfactory. It is mostly wrong, treats the term only literally when slang is required, says it does not know without giving a useful meaning, misses an essential part of the phrase, is too vague to be useful, or leaves the user with a false understanding.
- Score 1: The response is satisfactory. It accurately explains the slang meaning in a way a user could rely on. It may be brief and does not need to mention every nuance, origin, or usage caveat.

Return exactly two lines:
Score: <0 or 1>
Description: <one sentence explaining which elements of the response drove your score>
