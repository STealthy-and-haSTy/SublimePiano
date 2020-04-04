import sublime, sublime_plugin
import mido
import math
from dataclasses import dataclass
from typing import Iterable, NamedTuple
import itertools

output_name = None
rtmidi = mido.Backend('mido.backends.rtmidi')
out_port = None

@dataclass
class PianoInstruction:#(NamedTuple):
    span: sublime.Region
    scope: str
    text: str

@dataclass
class SequenceState:
    container_view_id: int #sublime.View
    tokens: Iterable[PianoInstruction]
    token_index: int = -1
    tempo: int = 120
    octave: int = 4
    note_length: int = 8
    current_token: PianoInstruction = None

    @staticmethod
    def new(view, regions):
        return SequenceState(
            container_view_id=view.id(),
            tokens=list(PianoInstruction(token[0], token[1], view.substr(token[0])) for token in itertools.chain(*(view.extract_tokens_with_scopes(region) for region in regions))),
        )

    def get_current_note_duration(self):
        return PianoMidi.calculate_duration(self.tempo, self.note_length)

    def advance_token(self):
        self.token_index += 1

        self.current_token = self.tokens[self.token_index] if self.token_index < len(self.tokens) else None
        # highlight the token region in the view
        view = sublime.View(self.container_view_id)
        if self.current_token:
            regions = [self.current_token.span]
            if sublime.score_selector(self.current_token.scope, 'constant.language.sharp, constant.numeric.integer.decimal'):
                regions[0] = regions[0].cover(self.tokens[self.token_index - 1].span)
            elif sublime.score_selector(self.current_token.scope, 'keyword.operator.simultaneous'):
                regions.clear()
                look_back = self.token_index - 1
                while look_back >= 0 and not sublime.score_selector(self.tokens[look_back].scope, 'keyword.operator.simultaneous'):
                    if sublime.score_selector(self.tokens[look_back].scope, 'constant.language.note, constant.language.sharp'):
                        regions.append(self.tokens[look_back].span)
                    look_back -= 1
            view.add_regions('piano_seq_current_note', regions, 'meta.piano-playing', '')
        else:
            view.erase_regions('piano_seq_current_note')

        return self.current_token is not None

    def peek_token(self):
        if self.token_index + 1 < len(self.tokens):
            return self.tokens[self.token_index + 1]
        return None

    def parse_note_token(self):
        note_index = (PianoMidi.notes_letters if self.current_token.span.size() == 1 else PianoMidi.notes_solfege).index(self.current_token.text)
        next_token = self.peek_token()
        if next_token and sublime.score_selector(next_token.scope, 'constant.language.sharp'):
            self.advance_token()
            note_index += 1
        return note_index

class PianoMidi:
    notes_solfege = 'do do# re re# mi fa fa# sol sol# la la# si'.split()
    notes_letters = 'c c# d d# e f f# g g# a a# b'.split()

    @staticmethod
    def note_to_midi_note(octave, note_index):
        return octave * len(PianoMidi.notes_solfege) + note_index

    @staticmethod
    def midi_note_to_note(note):
        note_index = note % len(PianoMidi.notes_solfege)
        #octave = (note - note_index) / len(PianoMidi.notes_solfege)
        octave = math.floor(note / len(PianoMidi.notes_solfege))
        return (octave, note_index)

    @staticmethod
    def calculate_duration(tempo: int, note_length: int):
        return (60 / tempo) / note_length * 4 * 1000

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

    note_sequences = list()

    def play_next_note_in_sequence(self, sequence: SequenceState):
        simultaneous_notes = None
        while sequence.advance_token():
            if sublime.score_selector(sequence.current_token.scope, 'keyword.operator.bitwise.octave'):
                if sequence.current_token.text == '<':
                    sequence.octave -= 1
                elif sequence.current_token.text == '>':
                    sequence.octave += 1
                else:
                    print('unknown octave operator token', sequence.current_token)
            elif sublime.score_selector(sequence.current_token.scope, 'keyword.operator.octave'):
                sequence.advance_token()
                sequence.octave = int(sequence.current_token.text)
            elif sublime.score_selector(sequence.current_token.scope, 'keyword.operator.tempo'):
                sequence.advance_token()
                sequence.tempo = int(sequence.current_token.text)
            elif sublime.score_selector(sequence.current_token.scope, 'keyword.operator.length'):
                sequence.advance_token()
                sequence.note_length = int(sequence.current_token.text)
            elif sublime.score_selector(sequence.current_token.scope, 'keyword.operator.pause'):
                sequence.advance_token()
                sublime.set_timeout_async(lambda: self.play_next_note_in_sequence(sequence), sequence.get_current_note_duration() if sequence.current_token.text == '0' else PianoMidi.calculate_duration(sequence.tempo, int(sequence.current_token.text)))
                break
            elif sublime.score_selector(sequence.current_token.scope, 'constant.language.note'):
                note_index = sequence.parse_note_token()

                if simultaneous_notes is None:
                    self.play_note_with_duration(sequence.octave, note_index, sequence.get_current_note_duration())
                    sublime.set_timeout_async(lambda: self.play_next_note_in_sequence(sequence), sequence.get_current_note_duration())
                    break
                else:
                    simultaneous_notes.append((sequence.octave, note_index, sequence.get_current_note_duration()))
            elif sublime.score_selector(sequence.current_token.scope, 'keyword.operator.simultaneous'):
                if simultaneous_notes is None:
                    simultaneous_notes = list()
                else:
                    longest_duration = 0
                    for octave, note_index, duration in simultaneous_notes:
                        if duration > longest_duration:
                            longest_duration = duration
                        self.play_note_with_duration(octave, note_index, duration)

                    simultaneous_notes = None
                    sublime.set_timeout_async(lambda: self.play_next_note_in_sequence(sequence), longest_duration)
                    break
            # TODO: think about references to labels - ideally highlight both the current note and where the label was called from

        if not sequence.current_token:
            try:
                self.note_sequences.remove(sequence)
            except KeyError:
                pass

class Piano(sublime_plugin.ViewEventListener, PianoMidi):
    @classmethod
    def is_applicable(cls, settings):
        syntax = settings.get('syntax')
        return syntax.endswith('/piano.sublime-syntax')

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
        note_color_scope = 'meta.piano-playing' if out_port else 'region.purplish' # TODO: make it configurable what color is used when no midi port is open
        self.view.add_regions(Piano.region_key_for_note(octave, note_index), key_bounds, note_color_scope, '', sublime.DRAW_NO_OUTLINE)

    def turn_key_color_off(self, octave, note_index):
        self.view.erase_regions(Piano.region_key_for_note(octave, note_index))

    def note_on(self, octave, note_index):
        self.draw_key_in_color(octave, note_index)
        super().note_on(octave, note_index)

    def note_off(self, octave, note_index):
        super().note_off(octave, note_index)
        self.turn_key_color_off(octave, note_index)

class PianoTune(sublime_plugin.ViewEventListener, PianoMidi):
    @classmethod
    def is_applicable(cls, settings):
        syntax = settings.get('syntax')
        return syntax.endswith('/PianoTune.sublime-syntax')

    def find_piano(self):
        variables = self.view.window().extract_variables()
        variables.update({ 'package_name': __name__.split('.')[0] })
        piano = self.view.window().find_open_file(sublime.expand_variables('$packages/$package_name/piano_ascii.txt', variables))
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

    # TODO: think about showing the octave a note is in when hovered over as a phantom or annotation or popup?

def plugin_loaded():
    output_name = mido.get_output_names()[0] # TODO: what about when Qsynth is started after ST/ this plugin? command to show a quick panel and change the output - store it in settings and load it instead of this default
    print('piano: using midi output:', output_name)
    global out_port
    out_port = rtmidi.open_output(output_name)

def plugin_unloaded():
    if out_port:
        out_port.close()

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
        seq = SequenceState.new(self.view, regions)
        listener.note_sequences.append(seq)
        listener.play_next_note_in_sequence(seq)

    def is_enabled(self):
        listener = sublime_plugin.find_view_event_listener(self.view, PianoTune)
        return listener is not None

class ConvertPianoNotesCommand(sublime_plugin.TextCommand):
    def run(self, edit, convert_to='toggle_notation'):
        # this will use the syntax def to convert the notation
        # TODO: utilize the Sequence?

        regions = self.view.sel()
        if len(regions) == 1 and regions[0].empty():
            regions = [sublime.Region(0, self.view.size())]

        for region in reversed(regions):
            for span, scope in reversed(self.view.extract_tokens_with_scopes(region)):
                if sublime.score_selector(scope, 'constant.language.note') > 0:
                    if convert_to == 'toggle_notation':
                        convert_to = 'solfege' if span.size() == 1 else 'letter'

                    from_notes = PianoMidi.notes_solfege if convert_to == 'letter' else PianoMidi.notes_letters
                    to_notes = PianoMidi.notes_letters if convert_to == 'letter' else PianoMidi.notes_solfege

                    if (span.size() == 1) == (convert_to != 'letter'):
                        self.view.replace(edit, span, to_notes[from_notes.index(self.view.substr(span).lower())])

    def is_enabled(self):
        return any((self.view.match_selector(region.begin(), 'text.piano-tune') for region in self.view.sel()))

class StopPianoNotesCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        listener = sublime_plugin.find_view_event_listener(self.view, PianoTune)
        for seq in listener.note_sequences:
            seq.token_index = len(seq.tokens)

    def is_enabled(self):
        listener = sublime_plugin.find_view_event_listener(self.view, PianoTune)
        # TODO: only show if listener.note_sequences is not empty?
        return listener is not None

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
        octave = 3 + math.floor(index / len(PianoMidi.notes_solfege))
        note_index = (index % len(PianoMidi.notes_solfege))

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
