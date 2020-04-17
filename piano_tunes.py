import sublime
from dataclasses import dataclass
from typing import Iterable, NamedTuple
import mido
from abc import ABC
from operator import itemgetter, attrgetter


class Token(NamedTuple):
    region: sublime.Region
    scope: str
    text: str

@dataclass
class TuneInstruction(ABC):
    span: sublime.Region
    value: int

@dataclass
class NoteInstruction(TuneInstruction):
    pass

@dataclass
class MultipleNotesDelimiterInstruction(TuneInstruction):
    pass

@dataclass
class LengthInstruction(TuneInstruction):
    pass

@dataclass
class AbsoluteOctaveInstruction(TuneInstruction):
    pass

@dataclass
class RelativeOctaveInstruction(TuneInstruction):
    pass

@dataclass
class TempoInstruction(TuneInstruction):
    pass

@dataclass
class PauseInstruction(TuneInstruction):
    pass

@dataclass
class MidiMessageOrInstruction(ABC):
    pass

@dataclass
class MidiMessageOrInstruction_MidiMessage(MidiMessageOrInstruction):
    msg: mido.Message

@dataclass
class MidiMessageOrInstruction_TuneInstruction(MidiMessageOrInstruction):
    instruction: TuneInstruction
    on: bool = True

@dataclass
class NoteInfo:
    octave: int
    note_token: NoteInstruction
    start_time: float
    duration: float
    active_pause: Iterable[Token]


def note_to_midi_note(octave, note_index):
    return octave * 12 + note_index

def get_tokens_from_regions(view, regions):
    for region in regions:
        tokens = view.extract_tokens_with_scopes(region)
        # TODO: maybe better performance-wise to grab all the text from the region at once, rather than requesting it for each individual token separately
        #text = view.substr(region)

        for token in tokens:
            yield Token(region=token[0], scope=token[1], text=view.substr(token[0]))

def parse_piano_tune(tokens: Iterable[Token]):
    notes_solfege = 'do do# re re# mi fa fa# sol sol# la la# si'.split() # TODO: reuse this from PianoMidi
    notes_letters = 'c c# d d# e f f# g g# a a# b'.split()

    it = iter(tokens)
    take_next = True
    while True:
        if take_next:
            current_token = next(it, None)
        else:
            take_next = True
        if current_token is None:
            break

        start_region = current_token.region

        if sublime.score_selector(current_token.scope, 'keyword.operator.bitwise.octave'):
            if current_token.text == '<':
                yield RelativeOctaveInstruction(current_token.region, -1)
            elif current_token.text == '>':
                yield RelativeOctaveInstruction(current_token.region, 1)
        elif sublime.score_selector(current_token.scope, 'keyword.operator.octave'):
            current_token = next(it)
            yield AbsoluteOctaveInstruction(current_token.region.cover(start_region), int(current_token.text))
        elif sublime.score_selector(current_token.scope, 'keyword.operator.tempo'):
            current_token = next(it)
            yield TempoInstruction(current_token.region.cover(start_region), int(current_token.text))
        elif sublime.score_selector(current_token.scope, 'keyword.operator.length'):
            current_token = next(it)
            yield LengthInstruction(current_token.region.cover(start_region), int(current_token.text))
        elif sublime.score_selector(current_token.scope, 'keyword.operator.pause'):
            current_token = next(it)
            yield PauseInstruction(current_token.region.cover(start_region), int(current_token.text))
        elif sublime.score_selector(current_token.scope, 'constant.language.note'):
            find_note_in = notes_letters if current_token.region.size() == 1 else notes_solfege
            note_index = find_note_in.index(current_token.text.lower())
            
            prev_token = current_token
            current_token = next(it, None)
            if current_token and sublime.score_selector(current_token.scope, 'constant.language.sharp'):
                note_index += 1
                yield NoteInstruction(current_token.region.cover(start_region), note_index)
            else:
                take_next = False
                yield NoteInstruction(prev_token.region, note_index)
        elif sublime.score_selector(current_token.scope, 'keyword.operator.simultaneous'):
            yield MultipleNotesDelimiterInstruction(current_token.region, 0)
        # TODO: labels and label references

def calculate_duration(tempo: int, note_length: int):
        return (60 / tempo) / note_length * 4 * 1000

def convert_piano_tune_to_midi(tokens):
        simultaneous_notes = None
        tempo = 120
        current_octave = 4
        current_length = 8
        delta_time = 0
        active_pause = list()

        it = iter(tokens)
        while True:
            try:
                token = next(it)
            except StopIteration:
                break

            if isinstance(token, RelativeOctaveInstruction):
                current_octave += token.value
            elif isinstance(token, AbsoluteOctaveInstruction):
                current_octave = token.value
            elif isinstance(token, TempoInstruction):
                tempo = token.value
            elif isinstance(token, LengthInstruction):
                current_length = token.value
            elif isinstance(token, PauseInstruction):
                delta_time += calculate_duration(tempo, token.value or current_length)
                if simultaneous_notes is None:
                    yield MidiMessageOrInstruction_TuneInstruction(token)
                active_pause.append(token)
            elif isinstance(token, NoteInstruction):
                if simultaneous_notes is None:
                    yield MidiMessageOrInstruction_MidiMessage(mido.Message('note_on', note=note_to_midi_note(current_octave, token.value), time=delta_time))
                    for pause in active_pause:
                        yield MidiMessageOrInstruction_TuneInstruction(pause, False)
                    yield MidiMessageOrInstruction_TuneInstruction(token)
                    yield MidiMessageOrInstruction_MidiMessage(mido.Message('note_off', note=note_to_midi_note(current_octave, token.value), time=calculate_duration(tempo, current_length)))
                    yield MidiMessageOrInstruction_TuneInstruction(token, False)
                    delta_time = 0
                else:
                    simultaneous_notes.append(NoteInfo(current_octave, token, delta_time, calculate_duration(tempo, current_length), active_pause[:]))
                active_pause.clear()
            elif isinstance(token, MultipleNotesDelimiterInstruction):
                if simultaneous_notes is None:
                    simultaneous_notes = list()
                else:
                    active_pause.clear()
                    # sort the notes by start time then duration
                    # to achieve this, we first sort by duration
                    # then, by start time
                    # see https://docs.python.org/3/howto/sorting.html#sort-stability-and-complex-sorts
                    simultaneous_notes.sort(key=attrgetter('duration'))
                    simultaneous_notes.sort(key=attrgetter('start_time'))
                    
                    # active_pause highlight should be for next note, not current one
                    # i.e. always look a note ahead
                    pause = list()
                    for note in reversed(simultaneous_notes):
                        temp = note.active_pause
                        note.active_pause = pause
                        pause = temp

                    # l4 do p8 l8 re == do for 1/4, after 1/8, re will start, and do and re will end at the same time
                    time_elapsed = 0
                    first_note_on_index = 0
                    for index in range(0, len(simultaneous_notes)):
                        note_on = simultaneous_notes[index]

                        # turn off notes which no longer apply
                        for turn_off_index in range(first_note_on_index, index):
                            note_off = simultaneous_notes[turn_off_index]
                            note_end_time = note_off.start_time + note_off.duration
                            if note_end_time <= note_on.start_time:
                                first_note_on_index = turn_off_index + 1
                                yield MidiMessageOrInstruction_MidiMessage(mido.Message('note_off', note=note_to_midi_note(note_off.octave, note_off.note_token.value), time=note_end_time - time_elapsed))
                                yield MidiMessageOrInstruction_TuneInstruction(note_off.note_token, False)
                                # for pause in note_off.active_pause: # TODO: this turns the pause off too late - ideally we would send a dummy Midi instruction with the right timing for the pause to turn off...
                                #     yield MidiMessageOrInstruction_TuneInstruction(pause, False)

                                time_elapsed = note_end_time

                        # for pause in note_on.active_pause:
                        #     yield MidiMessageOrInstruction_TuneInstruction(pause)
                        yield MidiMessageOrInstruction_MidiMessage(mido.Message('note_on', note=note_to_midi_note(note_on.octave, note_on.note_token.value), time=note_on.start_time - time_elapsed))
                        yield MidiMessageOrInstruction_TuneInstruction(note_on.note_token)

                        time_elapsed = note_on.start_time

                    for turn_off_index in range(first_note_on_index, index + 1):
                        # TODO: refactor slightly to reuse the code from in the loop above
                        #       so modifications to one won't be forgotten in the other
                        note_off = simultaneous_notes[turn_off_index]
                        note_end_time = note_off.start_time + note_off.duration
                        yield MidiMessageOrInstruction_MidiMessage(mido.Message('note_off', note=note_to_midi_note(note_off.octave, note_off.note_token.value), time=note_end_time - time_elapsed))
                        yield MidiMessageOrInstruction_TuneInstruction(note_off.note_token, False)
                        # for pause in note_off.active_pause:
                        #     yield MidiMessageOrInstruction_TuneInstruction(pause, False)
                        time_elapsed = note_end_time

                    delta_time = 0
                    simultaneous_notes = None
            # TODO: think about references to labels - ideally highlight both the current note and where the label was called from
