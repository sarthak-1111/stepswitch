# The README's demo: one task whose steps run on three models. A tiny local model plans and
# summarizes, a large model does the work, and a model of another family reviews.
# `uv run python scripts/record_demo.py` records it in VHS's container image after setting up
# the toy project and the models (see that script). The recording plays at twice the speed.
Output docs/assets/demo.gif

Require ssw

Set Shell "bash"
Set FontSize 15
Set Width 1280
Set Height 720
Set Padding 18
Set TypingSpeed 30ms
Set Framerate 12
Set PlaybackSpeed 2
Set WaitTimeout 20m

Hide
Type "cd /work/project && export PS1='$ ' && clear"
Enter
Sleep 1s
Show

Type "ssw run 'Fix the bug in add() in calc.py so that the tests pass.'"
Sleep 600ms
Enter
Wait+Screen /Trajectory:/
Sleep 6s
