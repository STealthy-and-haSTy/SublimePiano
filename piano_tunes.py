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

class TuneState(NamedTuple):
    tempo: int
    current_octave: int
    current_length: int
    time_elapsed: float
    simultaneous_notes: bool = False
    instruction: TuneInstruction = None
    duration: float = 0

@dataclass
class PianoTuneMidiHighlight:
    state: TuneState
    on: bool
    time_elapsed: float


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

def resolve_piano_tune_instructions(instructions: Iterable[NoteInstruction], default_state=TuneState(120, 4, 8, 0, False, None, 0)):
    state = default_state
    time_elapsed = 0
    max_time_elapsed = 0

    for token in instructions:
        time_elapsed = state.time_elapsed
        if not state.simultaneous_notes or not isinstance(state.instruction, NoteInstruction):
            time_elapsed += state.duration
            max_time_elapsed = max(time_elapsed, max_time_elapsed)
        else:
            max_time_elapsed = max(time_elapsed + state.duration, max_time_elapsed)
        state = state._replace(instruction=token, time_elapsed=time_elapsed, duration=0)
        if isinstance(token, RelativeOctaveInstruction):
            state = state._replace(current_octave=state.current_octave + token.value)
        elif isinstance(token, AbsoluteOctaveInstruction):
            state = state._replace(current_octave=token.value)
        elif isinstance(token, TempoInstruction):
            state = state._replace(tempo=token.value)
        elif isinstance(token, LengthInstruction):
            state = state._replace(current_length=token.value)
        elif isinstance(token, PauseInstruction):
            state = state._replace(duration=calculate_duration(state.tempo, token.value or state.current_length))
        elif isinstance(token, NoteInstruction):
            state = state._replace(duration=calculate_duration(state.tempo, state.current_length))
        if isinstance(token, MultipleNotesDelimiterInstruction):
            state = state._replace(simultaneous_notes=not state.simultaneous_notes, time_elapsed=max_time_elapsed)
        # TODO: think about references to labels - ideally highlight both the current note and where the label was called from... possibly even nested labels too?
        yield state

def convert_piano_tune_to_midi(tune_states):
    # reduce states to those that are notes or something to highlight, like pauses
    def state_is_interesting(state):
        return isinstance(state.instruction, NoteInstruction) \
            or isinstance(state.instruction, PauseInstruction)
            #TODO: labels/references for highlighting
    tune_states = list(state for state in tune_states if state_is_interesting(state))

    # for each state, add an "off" state after the duration
    add_states = list()
    for state in tune_states:
        add_states.append(
            PianoTuneMidiHighlight(state, True, state.time_elapsed)
        )
        add_states.append(
            PianoTuneMidiHighlight(state, False, state.time_elapsed + state.duration)
        )
    add_states.sort(key=attrgetter('time_elapsed'))
    return add_states
