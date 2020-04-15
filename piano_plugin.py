import sublime, sublime_plugin
import mido
import mimetypes
import time
from dataclasses import dataclass
from typing import Iterable, NamedTuple
import itertools
import threading
from . import piano_tunes


### ---------------------------------------------------------------------------


in_port = None
out_port = None


### ---------------------------------------------------------------------------


def plugin_loaded():
    piano_prefs.obj = sublime.load_settings('piano.sublime-settings')
    piano_prefs.default = {
        "input_name": None,
        "output_name": None,
        "program": None,

        "piano_layout": "piano_7octave",

        # TODO: In the code, this was region.redish, but in the settings file
        # it's string; which is the one we want?
        "scope_to_highlight_current_piano_tune_note": "string",
    }

    port_changed('in', piano_prefs('input_name'))
    port_changed('out', piano_prefs('output_name'))


def plugin_unloaded():
    PlayMidiFileCommand.playing_file = None
    port_changed('in', None)
    port_changed('out', None)


def get_res_name(res_stub):
    package_name = __name__.split('.')[0]
    return 'Packages/' + package_name + '/' + res_stub


def piano_prefs(key, new_value=None):
    if new_value is not None:
        piano_prefs.obj.set(key, new_value)
        return sublime.save_settings('piano.sublime-settings')

    default = piano_prefs.default.get(key, None)
    return piano_prefs.obj.get(key, default)


def get_available_port_names(port_type):
    available_port_names = mido.get_output_names() if port_type == 'out' else mido.get_input_names()
    current_port_name = piano_prefs(port_type + 'put_name')
    try:
        pre_select_index = available_port_names.index(current_port_name)
    except ValueError:
        pre_select_index = -1
    return (available_port_names, 0 if current_port_name is None else pre_select_index)


def port_changed(port_type, port_name):
    global in_port
    global out_port

    if port_type == 'out':
        if out_port:
            out_port.reset()
            out_port.close()
    elif port_type == 'in':
        if in_port:
            in_port.close()

    print('piano: using midi ' + port_type + 'put:', port_name)

    if port_name:
        if port_name in get_available_port_names(port_type)[0]:
            # NOTE: we only update the preferences if a valid port has been set
            # TODO: do we want to have an option to clear an input port AND save that in the preferences?
            #       - and then make sure the input port isn't automatically opened when the plugin reloads?
            piano_prefs(port_type + 'put_name', port_name)
        else:
            print('piano: unable to find preferred ' + port_type + 'put port with name "' + port_name + '"')
            port_name = None

    # If there's no port, we don't want to try to open anything.
    if port_name is None:
        return

    if port_type == 'out':
        out_port = mido.open_output(port_name)
        program_changed(piano_prefs('program'))
    elif port_type == 'in':
        in_port = mido.open_input(port_name, callback=handle_midi_input)


def program_changed(program, save=False):
    if program is None:
        return

    if save:
        piano_prefs("program", program)

    msg = mido.Message('program_change', program=program)
    out_port.send(msg)


def handle_midi_input(msg):
    # Only handle the message if the piano has the focus; could also find the
    # piano view in the window as the other command does. Note: there is not
    # always an active view.
    view = sublime.active_window().active_view()
    if not view:
        return

    listener = sublime_plugin.find_view_event_listener(view, Piano)
    if listener:
        # Ship the message over; this will play notes, but also allow for
        # program changes, etc. This lets incoming velocity and aftertouch
        # information through without the event listener needing to  synthesize
        # them
        out_port.send(msg)

        # For note messges, we want to synthesize the display.
        if msg.type.startswith('note_'):
            octave, note = PianoMidi.midi_note_to_note(msg.note)

            # Per the specs, note_on with a velocity of 0 should be interpreted
            # as note_off; if that happens replace the message so the display
            # will update.
            if msg.type == 'note_on' and msg.velocity == 0:
                msg = mido.Message('note_off', note=msg.note, time=msg.time)

            # Get the listener to update the display but not play the note.
            sublime.set_timeout(lambda: getattr(listener, msg.type)(octave, note, False))

        return True

    return False


def set_piano_layout(piano_view, piano_layout):
    try:
        layout = sublime.load_resource(get_res_name('data/%s.piano_layout' % piano_layout))

        piano_view.set_read_only(False)
        piano_view.run_command('select_all')
        piano_view.run_command('left_delete')
        piano_view.run_command('append', {'characters': layout, 'disable_tab_translation': True})
        piano_view.set_read_only(True)

        # Save the layout used for later.
        piano_view.settings().set('piano_layout', piano_layout)

        return True
    except:
        piano_view.window().status_message("Unable to find piano layout '%s'" % piano_layout)
        return False


def get_piano_view(create=False, focus=False, piano_layout=None):
    """
    Find and return the piano view, if any. If there's not a view but create
    is True, then this will create the view. If a view would be returned and
    focus is True, this will ensure that the view has the focus even if it has
    to raise the window to do it.

    When creating a view, piano_layout specifies the file to load. If there is
    a view and piano_layout is given, then the layout is swapped to the new one
    prior to return.
    """
    piano_view = None
    for window in sublime.windows():
        for view in window.views():
            if view.name() == 'Piano':
                piano_view = view
                break

    # If there's a view and we also got a layout, then change it
    if piano_view and piano_layout:
        if piano_view.settings().get('piano_layout') != piano_layout:
            set_piano_layout(piano_view, piano_layout)

    # If there is not a view and we were asked to create one, do it.
    if not piano_view and create:
        piano_layout = piano_layout or piano_prefs('piano_layout')

        window = sublime.active_window()

        piano_view = window.new_file(syntax=get_res_name('piano.sublime-syntax'))
        piano_view.set_name('Piano')
        piano_view.set_scratch(True)

        piano_view.settings().set('is_piano', True)

        # TODO: What if the layout is invalid here; what should be returned
        set_piano_layout(piano_view, piano_layout)

    # If there's a view and we were asked to do, focus it.
    if piano_view and focus:
        piano_view.window().bring_to_front()
        piano_view.window().focus_view(piano_view)

    return piano_view


### ---------------------------------------------------------------------------


class ShowPianoCommand(sublime_plugin.ApplicationCommand):
    def run(self, piano_layout=None):
        piano_layout = piano_layout or piano_prefs('piano_layout')
        get_piano_view(create=True, focus=True, piano_layout=piano_layout)


class PlayPianoNotesCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        listener = sublime_plugin.find_view_event_listener(self.view, PianoTune)
        # take the notes from the selection or entire buffer
        regions = self.view.sel()
        if len(regions) == 1 and regions[0].empty():
            regions = [sublime.Region(0, self.view.size())]
        # TODO: when playing a selection, should it automatically find the octave and tempo, from before the selection, or expect the user to select those too?
        # TODO: think about how left hand vs right hand vs both hand playing could work
        #       - should they be in separate files, or marked up in a single file?
        #         - maybe easier to understand the files if separate, especially with labels etc.
        # TODO: a mode where only the keys light up without the sound playing
        #       - and a mode where it waits for the user to press the key before continuing on
        #         - here the left and right hand (i.e. if user is playing right hand and left is on auto-play) need to stay synced up
        tokens = piano_tunes.parse_piano_tune(piano_tunes.get_tokens_from_regions(self.view, regions))
        #print(list(tokens))

        midi_messages = piano_tunes.convert_piano_tune_to_midi(tokens)
        #print(list(midi_messages))
        listener.play_midi_instructions(midi_messages)

    def is_enabled(self):
        listener = sublime_plugin.find_view_event_listener(self.view, PianoTune)
        return listener is not None


class StopPianoNotesCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        listener = sublime_plugin.find_view_event_listener(self.view, PianoTune)
        listener.playback_stopped = True

    def is_enabled(self):
        listener = sublime_plugin.find_view_event_listener(self.view, PianoTune)
        return listener is not None and not listener.playback_stopped


class ResetMidiPortCommand(sublime_plugin.ApplicationCommand):
    def run(self, port_type='out'):
        out_port_name = out_port.name if out_port is not None else piano_prefs(port_type + 'put_name')
        port_changed(port_type, out_port_name)
        # TODO: currently any piano ascii views don't refresh to clear all active keys


class ConvertPianoTuneNotationCommand(sublime_plugin.TextCommand):
    def run(self, edit, convert_to='toggle_notation'):
        # this will use the syntax def to convert the notation

        regions = self.view.sel()
        if len(regions) == 1 and regions[0].empty():
            regions = [sublime.Region(0, self.view.size())]

        tokens = list(token for token in piano_tunes.parse_piano_tune(piano_tunes.get_tokens_from_regions(self.view, regions)) if isinstance(token, piano_tunes.NoteInstruction))
        if not tokens:
            # no notes to convert
            return

        if convert_to == 'toggle_notation':
            convert_to = 'solfege' if self.view.substr(tokens[0].span).lower() in PianoMidi.notes_letters else 'letter'
        to_notes = PianoMidi.notes_letters if convert_to == 'letter' else PianoMidi.notes_solfege

        for token in reversed(tokens):
            if isinstance(token, piano_tunes.NoteInstruction):
                self.view.replace(edit, token.span, to_notes[token.value])

    def is_enabled(self):
        return any((self.view.match_selector(region.begin(), 'text.piano-tune') for region in self.view.sel()))


class PlayMidiFileCommand(sublime_plugin.ApplicationCommand):
    """
    Control playback of a midi file; play any midi file by name, or stop the
    current playback. If no midi file name is provided as an argument, the
    name of the current file is used instead (which needs to be a midi file).

    Playback can't start if the command is already playing something.
    """
    midi = None

    def run(self, stop=False, midi_filename=None):
        if stop:
            PlayMidiFileCommand.midi = None
            if out_port:
                out_port.reset()
                program_changed(piano_prefs('program'))

            return

        midi_filename = self.filename(midi_filename)
        threading.Thread(target=lambda: self.play(midi_filename)).start()

    def filename(self, file_name):
        if file_name:
            return file_name

        view = sublime.active_window().active_view()
        return view.file_name() if view is not None else None

    def play(self, file_name):
        try:
            PlayMidiFileCommand.midi = mido.MidiFile(file_name)
            for msg in PlayMidiFileCommand.midi.play():
                if not PlayMidiFileCommand.midi:
                    return

                if not handle_midi_input(msg):
                    out_port.send(msg)

            sublime.active_window().status_message("Midi playback complete")
        except:
            sublime.active_window().status_message("Midi playback error")
            raise
        finally:
            PlayMidiFileCommand.midi = None
            if out_port:
                out_port.reset()
                program_changed(piano_prefs('program'))

    def is_enabled(self, stop=False, midi_filename=None):
        # If we're being asked to stop, whether we can or not is determined by
        # whether we're playing or not.
        if stop:
            return PlayMidiFileCommand.midi is not None

        # We can't play if playback is already started
        if PlayMidiFileCommand.midi is not None:
            return False

        # We can only play if we got a filename that appears to be midi
        name = self.filename(midi_filename)
        return out_port is not None and mimetypes.guess_type(name or 'unknown')[0] in ('audio/mid', 'audio/midi')


class PickMidiProgramCommand(sublime_plugin.ApplicationCommand):
    inst_list = None

    def run(self, program=None):
        self.load_instruments()
        window = sublime.active_window()

        def pick(items, group, index):
            if index >= 0:
                if group is None:
                    group = items[index]
                    programs = [inst["name"] for inst in self.inst_list.get(group, [])]
                    programs.insert(0, '..')
                    return window.show_quick_panel(programs, lambda idx: pick(programs, group, idx))

                if index == 0:
                    groups = self.inst_list.get("program_groups")
                    return window.show_quick_panel(groups, lambda idx: pick(groups, None, idx))

                # Picked an instrument
                program = self.inst_list.get(group)[index -1]
                window.run_command("pick_midi_program", {"program": program["program"]})

        if program is None:
            groups = self.inst_list.get("program_groups")
            return window.show_quick_panel(groups, lambda idx: pick(groups, None, idx))

        program_changed(program, save=True)

    def load_instruments(self):
        if self.inst_list is None:
            try:
                data = sublime.load_resource(get_res_name('data/instruments.json'))
                self.inst_list = sublime.decode_value(data)
            except:
                sublime.active_window().status_message('Error loading instruments')
                return {}

        return self.inst_list

    def is_enabled(self, instrument=None):
        return out_port is not None


class PlayPianoNoteFromPcKeyboardCommand(sublime_plugin.TextCommand):
    active_notes = dict()

    def run(self, edit, character):
        listener = sublime_plugin.find_view_event_listener(self.view, Piano)

        try:
            # NOTE: keyboard layout dependent... needs to be configurable with some presets (QWERTY/colemak etc.)
            # these were taken from the virtual piano audiosynth.js project - http://keithwhor.com/music/
            index = 'q2w3er5t6y7ui9o0p[=]azsxcfvgbnjmk,l.'.index(character)
        except ValueError:
            return

        # left most key in the list starts at octave 3
        octave = 3 + index // len(PianoMidi.notes_solfege)
        note_index = index % len(PianoMidi.notes_solfege)

        note = (octave, note_index)
        # if the note is already playing, just extend the time out rather than playing it again
        if note in self.active_notes.keys():
            self.active_notes[note] += 1
            timeout = 96
        else:
            self.active_notes[note] = 1
            timeout = 500 # key repeat delay
            listener.note_on(octave, note_index)
        sublime.set_timeout_async(lambda: self.stop_or_extend_note(note), timeout)

    def stop_or_extend_note(self, note):
        self.active_notes[note] -= 1
        if self.active_notes[note] <= 0:
            del self.active_notes[note]
            listener = sublime_plugin.find_view_event_listener(self.view, Piano)
            listener.note_off(*note)

    def is_enabled(self):
        return sublime_plugin.find_view_event_listener(self.view, Piano) is not None


class PickMidiPort(sublime_plugin.WindowCommand):
    def run(self, port_type='out'):
        items, pre_select_index = get_available_port_names(port_type)
        if len(items) == 0:
            sublime.message_dialog('no ' + port_type + 'put ports found')
            port_changed(port_type, None)
        else:
            self.window.show_quick_panel(items, lambda index: port_changed(port_type, items[index] if index > -1 else None), flags=0, selected_index=pre_select_index)


### ---------------------------------------------------------------------------


class PianoMidi:
    notes_solfege = 'do do# re re# mi fa fa# sol sol# la la# si'.split()
    notes_letters = 'c c# d d# e f f# g g# a a# b'.split()

    @staticmethod
    def note_to_midi_note(octave, note_index):
        return octave * len(PianoMidi.notes_solfege) + note_index

    @staticmethod
    def midi_note_to_note(note):
        note_index = note % len(PianoMidi.notes_solfege)
        octave = note // len(PianoMidi.notes_solfege)
        return (octave, note_index)

    def note_on(self, octave, note_index):
        if out_port:
            out_port.send(mido.Message('note_on', note=PianoMidi.note_to_midi_note(octave, note_index)))

    def play_note_with_duration(self, octave, note_index, duration):
        self.note_on(octave, note_index)
        # schedule the note to be turned off again
        sublime.set_timeout_async(lambda: self.note_off(octave, note_index), duration)

    def note_off(self, octave, note_index):
        if out_port:
            out_port.send(mido.Message('note_off', note=PianoMidi.note_to_midi_note(octave, note_index)))


class Piano(sublime_plugin.ViewEventListener, PianoMidi):
    @classmethod
    def is_applicable(cls, settings):
        return settings.get('is_piano', False)

    def on_post_text_command(self, command_name, args):
        if command_name == 'drag_select': # TODO: when clicking, keep the note playing for as long as the mouse button is pressed for
            for sel in self.view.sel():
                if self.view.match_selector(sel.begin(), 'meta.piano-instrument.piano'):
                    self.play_note_from_piano_at_position(sel.begin())

    def play_note_from_piano_at_position(self, pos):
        row, col = self.view.rowcol(pos)
        if self.view.match_selector(pos, 'punctuation.section.key.piano'):
            # when the caret is on the key border line, we want to get the scope of the char to the left
            col -= 1
            pos -= 1

        scope_atoms = self.view.scope_name(pos).strip().split(' ')[-1].split('.')
        if scope_atoms[0] in ('punctuation', 'meta'):
            return

        note_index = int(scope_atoms[3][len('midi-'):])

        # to find the octave, we count the number of 'DO' keys between col 0 and the key that was clicked on
        tokens_to_the_left = self.view.extract_tokens_with_scopes(sublime.Region(self.view.line(pos).begin(), pos + 1))
        octave = sum(1 for token in tokens_to_the_left if '.midi-0.' in token[1])

        self.play_note_with_duration(octave, note_index, 384)

    def get_key_region(self, octave, note_index):
        look_for = '.midi-' + str(note_index) + '.'
        try:
            piano_region = self.view.find_by_selector('meta.piano-instrument.piano')[0]
        except IndexError:
            return

        for line in self.view.lines(piano_region):
            current_octave = 0
            for token in self.view.extract_tokens_with_scopes(line):
                if look_for in token[1]:
                    current_octave += 1
                    if current_octave == octave:
                        yield token[0]
                        break

    @staticmethod
    def region_key_for_note(octave, note_index):
        return 'piano-midi-note-' + str(octave) + '-' + str(note_index)

    def draw_key_in_color(self, octave, note_index):
        key_bounds = list(self.get_key_region(octave, note_index))
        note_color_scope = 'meta.piano-playing' if out_port and not out_port.closed else 'meta.piano-playing-but-no-out-port'
        self.view.add_regions(Piano.region_key_for_note(octave, note_index), key_bounds, note_color_scope, '', sublime.DRAW_NO_OUTLINE)

    def turn_key_color_off(self, octave, note_index):
        self.view.erase_regions(Piano.region_key_for_note(octave, note_index))

    def note_on(self, octave, note_index, play=True):
        self.draw_key_in_color(octave, note_index)
        if play:
            super().note_on(octave, note_index)

    def note_off(self, octave, note_index, play=True):
        if play:
            super().note_off(octave, note_index)
        self.turn_key_color_off(octave, note_index)


class PianoTune(sublime_plugin.ViewEventListener, PianoMidi):
    @classmethod
    def is_applicable(cls, settings):
        syntax = settings.get('syntax')
        return syntax.endswith('/PianoTune.sublime-syntax')

    def find_piano(self):
        piano = get_piano_view()
        if piano:
            return sublime_plugin.find_view_event_listener(piano, Piano)

    def note_on(self, octave, note_index):
        listener = self.find_piano()
        if listener:
            listener.draw_key_in_color(octave, note_index)
        super().note_on(octave, note_index)

    def note_off(self, octave, note_index):
        super().note_off(octave, note_index)
        listener = self.find_piano()
        if listener:
            listener.turn_key_color_off(octave, note_index)

    playback_stopped = True

    def play_midi_instructions(self, messages: Iterable[piano_tunes.MidiMessageOrInstruction]):
        self.playback_stopped = False
        def play():
            current_instruction_regions = list()
            adjust = 0
            for item in messages:
                if isinstance(item, piano_tunes.MidiMessageOrInstruction_MidiMessage):
                    msg = item.msg
                    if msg.time > 0 and not self.playback_stopped:
                        before = time.perf_counter()
                        time.sleep(msg.time / 1000 - adjust)
                        elapsed = time.perf_counter() - before
                        adjust = elapsed - msg.time / 1000

                    if self.playback_stopped and msg.type == 'note_on':
                        # if playback has stopped, process all note_off messages
                        # - ignoring the timings. This saves us from having to reset
                        #   the output port
                        continue
                    octave, note = PianoMidi.midi_note_to_note(msg.note)
                    getattr(self, msg.type)(octave, note)
                else:
                    span = item.instruction.span
                    if item.on:
                        current_instruction_regions.append(span)
                    else:
                        current_instruction_regions.remove(span)
                    self.view.add_regions('piano_seq_current_note', current_instruction_regions, piano_prefs('scope_to_highlight_current_piano_tune_note'))
                    # when there are no notes being played, and playback has stopped, exit the loop
                    if not current_instruction_regions and self.playback_stopped:
                        break
            self.view.erase_regions('piano_seq_current_note')
            self.playback_stopped = True

        threading.Thread(target=play).start()

    # TODO: think about showing the octave a note is in when hovered over as a phantom or annotation or popup?


### ---------------------------------------------------------------------------
