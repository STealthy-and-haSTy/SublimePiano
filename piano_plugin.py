import sublime, sublime_plugin
import mido
import math
from .piano_notes import *

output = None
rtmidi = mido.Backend('mido.backends.rtmidi')
port = None
playing_note_scope = 'meta.piano-playing'

class Piano(sublime_plugin.ViewEventListener):
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

    @staticmethod
    def note_to_midi_note(octave, note_index):
        return octave * 12 + note_index
    
    @staticmethod
    def midi_note_to_note(note):
        note_index = note % 12
        #octave = (note - note_index) / 12
        octave = math.floor(note / 12)
        return (octave, note_index)

    def get_key_region(self, octave, note_index):
        look_for = '.midi-' + str(note_index) + '.'
        piano_region = self.view.find_by_selector('meta.piano-instrument.piano')[0]
        for line in self.view.lines(piano_region):
            current_octave = 0
            for token in self.view.extract_tokens_with_scopes(line):
                if look_for in token[1]:
                    current_octave += 1
                    if current_octave == octave:
                        yield token[0]
                        break

    def play_note(self, octave, note_index):
        note = Piano.note_to_midi_note(octave, note_index)
        self.play_midi_note(note)

    def play_midi_note(self, note):
        # draw key in color
        key_bounds = list(self.get_key_region(*Piano.midi_note_to_note(note)))
        region_key = 'piano-midi-note-' + str(note)
        note_color_scope = playing_note_scope if port else 'region.purplish' # TODO: make it configurable what color is used when no midi port is open
        self.view.add_regions(region_key, key_bounds, note_color_scope, '', sublime.DRAW_NO_OUTLINE)
        
        # play the note
        if port:
            port.send(mido.Message('note_on', note=note))

    def play_note_with_duration(self, octave, note_index, duration):
        self.play_note(octave, note_index)
        # schedule the note to be turned off again
        sublime.set_timeout_async(lambda: self.stop_note(octave, note_index), duration)

    def stop_midi_note(self, note):
        if port:
            port.send(mido.Message('note_off', note=note))
        # turn key color off
        region_key = 'piano-midi-note-' + str(note)
        self.view.erase_regions(region_key)

    def stop_note(self, octave, note_index):
        self.stop_midi_note(Piano.note_to_midi_note(octave, note_index))

    def play_note_sequence(self, notes):
        self.cancel_sequences = False
        if type(notes) is not list:
            notes = list(notes)
        
        tempo = 120
        def calculate_duration(length):
            return (60 / tempo) / length * 4 * 1000
        
        def play_next_note(note_index):
            if self.cancel_sequences:
                return

            instruction = notes[note_index]
            duration = 0
            
            global tempo
            if type(instruction) is PianoNote:
                current_note = instruction
                duration = calculate_duration(current_note.length)
                self.play_note_with_duration(current_note.octave, current_note.note_index, duration)
            elif type(instruction) is PianoNotes:
                # if the duration of the notes are different, schedule the next call back for the longest duration
                for current_note in instruction.notes:
                    current_duration = calculate_duration(current_note.length)
                    self.play_note_with_duration(current_note.octave, current_note.note_index, current_duration)
                    if current_duration > duration:
                        duration = current_duration
            else:
                if type(instruction) is Tempo:
                    tempo = instruction.bpm
                elif type(instruction) is Pause:
                    duration = calculate_duration(instruction.length)
            
            note_index += 1
            if note_index < len(notes):
                if duration == 0:
                    play_next_note(note_index)
                else:
                    sublime.set_timeout_async(lambda: play_next_note(note_index), duration + 60 / tempo)
        play_next_note(0)

def plugin_loaded():
    output = mido.get_output_names()[0] # TODO: what about when Qsynth is started after ST/ this plugin? command to show a quick panel and change the output - store it in settings and load it instead of this default
    print('piano: using midi output:', output)
    global port
    port = rtmidi.open_output(output)

def plugin_unloaded():
    if port:
        port.close()

class PickPianoNotesCommand(sublime_plugin.WindowCommand):
    def run(self):
        view = self.window.active_view()
        listener = sublime_plugin.find_view_event_listener(view, Piano)
        
        tunes = sublime.find_resources('*.piano-tune')
        # TODO: show quick panel
        view.run_command('play_piano_notes', { 'resource': tunes[0] })
    
    def is_enabled(self):
        view = self.window.active_view()
        listener = sublime_plugin.find_view_event_listener(view, Piano)
        return listener is not None

class PlayPianoNotesCommand(sublime_plugin.TextCommand):
    def run(self, edit, note_text=None, resource=None):
        listener = sublime_plugin.find_view_event_listener(self.view, Piano)
        if resource:
            note_text = sublime.load_resource(resource)
        # TODO: if note_text is None, take the notes from the selection or entire buffer
        if not listener:
            # TODO: support playing a piano-tune file directly without the piano open
            # TODO: but for now, open the piano ascii
            pass
        listener.play_note_sequence(get_midi_notes(note_text))
    
    def is_enabled(self):
        listener = sublime_plugin.find_view_event_listener(self.view, Piano)
        return listener is not None or self.view.syntax().endswith('/piano.sublime-syntax')
