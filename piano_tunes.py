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

# TODO: naming
@dataclass
class PianoTuneMidi(ABC):
    state: TuneState

@dataclass
class PianoTuneMidiMessage(PianoTuneMidi):
    msg: mido.Message

@dataclass
class PianoTuneMidiHighlight(PianoTuneMidi):
    on: bool
    time_delta: float


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

    for token in instructions:
        state = state._replace(instruction=token, time_elapsed=state.time_elapsed + (0 if state.simultaneous_notes and isinstance(state.instruction, NoteInstruction) else state.duration), duration=0)
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
            state = state._replace(simultaneous_notes=not state.simultaneous_notes)
            # TODO: need to add the total duration of the simultaneous notes that played when switching out of simultaneous mode
        # TODO: think about references to labels - ideally highlight both the current note and where the label was called from... possibly even nested labels too?
        yield state

def convert_piano_tune_to_midi(tune_states):
        tune_states = list(tune_states)
        # sort the instructions by start time then duration.
        # this is mostly already the case, but some simultaneous notes could have been
        # entered in a different order.
        # to achieve this, we first sort by duration and then sort again, by start time
        # see https://docs.python.org/3/howto/sorting.html#sort-stability-and-complex-sorts
        # TODO: instead, keep it as an iterator, and use a deque mechanism which would set
        #       the priority?
        tune_states.sort(key=attrgetter('duration'))
        tune_states.sort(key=attrgetter('time_elapsed'))
        
        # mido wants the duration between notes, so we need to calculate that
        # and to tell it when to turn the notes off again, as it doesn't allow
        # specifying a duration
        time_elapsed = 0
        active_states = list()
        for state in tune_states:
            # loop through active states to find which ones are no longer active
            for old_state in active_states:
                # if the next state's start time is before the end time of the active state, don't check any more active states
                if state.time_elapsed < old_state.time_elapsed + old_state.duration:
                    break
                delta_time = (old_state.time_elapsed + old_state.duration) - time_elapsed
                active_states.remove(old_state)
                if isinstance(old_state.instruction, NoteInstruction):
                    yield PianoTuneMidiMessage(old_state, mido.Message('note_off', note=note_to_midi_note(old_state.current_octave, old_state.instruction.value), time=delta_time))
                yield PianoTuneMidiHighlight(old_state, False, 0)
                time_elapsed += delta_time
            delta_time = state.time_elapsed - time_elapsed
            time_elapsed = state.time_elapsed

            if state.duration > 0: # TODO: or a label call...
                yield PianoTuneMidiHighlight(state, True, delta_time)
                active_states.append(state)
                if isinstance(state.instruction, NoteInstruction):
                    yield PianoTuneMidiMessage(state, mido.Message('note_on', note=note_to_midi_note(state.current_octave, state.instruction.value), time=0))

        for old_state in active_states:
            delta_time = old_state.time_elapsed + old_state.duration - time_elapsed
            if isinstance(old_state.instruction, NoteInstruction):
                yield PianoTuneMidiMessage(old_state, mido.Message('note_off', note=note_to_midi_note(old_state.current_octave, old_state.instruction.value), time=delta_time))
            yield PianoTuneMidiHighlight(old_state, False, 0)
            time_elapsed += delta_time
