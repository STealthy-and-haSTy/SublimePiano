# SublimePiano

A piano plugin for Sublime Text, which shows a virtual keyboard/piano which can play midi.

This is still in active development at the moment. Stay tuned!


# Installing

Currently, this plugin requires Sublime Text build 4050 or later, because it was easier to get/build the dependencies for Python 3.8 than for 3.3.

`python38 -m pip install -r requirements.txt --user`

Then copy the following from your `python3.8/site-packages/` folder into `~/.config/sublime-text/Lib/python38` (or equivalent if on another OS):
- `rtmidi/`
- `mido/`
- `pynput/` (currently not used)
- `Xlib/` (would be used by `pynput`)
- `six.py` (would be used by `pynput`)

Ensure you have `FluidSynth` installed (`sudo apt install fluidsynth`), and open QSynth. (If you have never used it before, click on Setup and go to the Soundfonts tab and open a sound font. You should only need to do this step once.)

# piano-notes

This is a custom DSL, loosely based on the old [QBASIC "PLAY" command syntax](https://www.qbasic.net/en/reference/qb11/Statement/PLAY-006.htm), but with support for Solfege style notation.
See the `tunes/` folder for examples.

I suggest having a 2 row layout, showing the piano in one row (Command Palette -> Show Piano), and opening a `piano-tune` file in the other row. Then, play it using the command palette entry.

# Help wanted

It would be great if one could connect a midi instrument and highlight the notes/keys pressed in ST. I have no such physical hardware / midi devices, so if someone wants to contribute support for this, I'd be happy to make you a maintainer.
