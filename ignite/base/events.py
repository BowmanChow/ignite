import numbers
import weakref
from enum import Enum
from types import DynamicClassAttribute
from typing import Any, Callable, Iterator, List, Optional, TYPE_CHECKING, Union

from ignite.engine.utils import _check_signature

if TYPE_CHECKING:
    from ignite.base.events_driven import EventsDriven
    from ignite.engine.engine import Engine
    from ignite.engine.events import Events


class CallableEventWithFilter:
    """Single Event containing a filter, specifying whether the event should
    be run at the current event (if the event type is correct)

    Args:
        value: The actual enum value. Only needed for internal use. Do not touch!
        event_filter: A function taking the engine and the current event value as input and returning a
            boolean to indicate whether this event should be executed. Defaults to None, which will result to a
            function that always returns `True`
        name: The enum-name of the current object. Only needed for internal use. Do not touch!
    """

    def __init__(self, value: str, event_filter: Optional[Callable] = None, name: Optional[str] = None) -> None:
        if event_filter is None:
            event_filter = CallableEventWithFilter.default_event_filter
        self.filter = event_filter

        if not hasattr(self, "_value_"):
            self._value_ = value

        if not hasattr(self, "_name_") and name is not None:
            self._name_ = name

    # copied to be compatible to enum
    @DynamicClassAttribute
    def name(self) -> str:
        """The name of the Enum member."""
        return self._name_

    @DynamicClassAttribute
    def value(self) -> str:
        """The value of the Enum member."""
        return self._value_

    def __call__(
        self, event_filter: Optional[Callable] = None, every: Optional[int] = None, once: Optional[int] = None
    ) -> "CallableEventWithFilter":
        """
        Makes the event class callable and accepts either an arbitrary callable as filter
        (which must take in the engine and current event value and return a boolean) or an every or once value

        Args:
            event_filter: a filter function to check if the event should be executed when
                the event type was fired
            every: a value specifying how often the event should be fired
            once: a value specifying when the event should be fired (if only once)

        Returns:
            CallableEventWithFilter: A new event having the same value but a different filter function
        """

        if not ((event_filter is not None) ^ (every is not None) ^ (once is not None)):
            raise ValueError("Only one of the input arguments should be specified")

        if (event_filter is not None) and not callable(event_filter):
            raise TypeError("Argument event_filter should be a callable")

        if (every is not None) and not (isinstance(every, numbers.Integral) and every > 0):
            raise ValueError("Argument every should be integer and greater than zero")

        if (once is not None) and not (isinstance(once, numbers.Integral) and once > 0):
            raise ValueError("Argument once should be integer and positive")

        if every is not None:
            if every == 1:
                # Just return the event itself
                event_filter = None
            else:
                event_filter = self.every_event_filter(every)

        if once is not None:
            event_filter = self.once_event_filter(once)

        # check signature:
        if event_filter is not None:
            _check_signature(event_filter, "event_filter", "engine", "event")

        return CallableEventWithFilter(self.value, event_filter, self.name)

    @staticmethod
    def every_event_filter(every: int) -> Callable:
        """A wrapper for every event filter."""

        def wrapper(engine: "Engine", event: int) -> bool:
            if event % every == 0:
                return True
            return False

        return wrapper

    @staticmethod
    def once_event_filter(once: int) -> Callable:
        """A wrapper for once event filter."""

        def wrapper(engine: "Engine", event: int) -> bool:
            if event == once:
                return True
            return False

        return wrapper

    @staticmethod
    def default_event_filter(engine: "Engine", event: int) -> bool:
        """Default event filter."""
        return True

    def __str__(self) -> str:
        return "<event=%s, filter=%r>" % (self.name, self.filter)

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, CallableEventWithFilter):
            return self.name == other.name
        elif isinstance(other, str):
            return self.name == other
        else:
            return NotImplemented

    def __hash__(self) -> int:
        return hash(self._name_)

    def __or__(self, other: Any) -> "EventsList":
        return EventsList() | self | other


class EventEnum(CallableEventWithFilter, Enum):
    """Base class for all :class:`~ignite.engine.events.Events`. User defined custom events should also inherit
    this class.

     Examples:
         Custom events based on the loss calculation and backward pass can be created as follows:

        .. code-block:: python

            from ignite.base.events import EventEnum

            class BackpropEvents(EventEnum):
                BACKWARD_STARTED = 'backward_started'
                BACKWARD_COMPLETED = 'backward_completed'
                OPTIM_STEP_COMPLETED = 'optim_step_completed'

            def update(engine, batch):
                # ...
                loss = criterion(y_pred, y)
                engine.fire_event(BackpropEvents.BACKWARD_STARTED)
                loss.backward()
                engine.fire_event(BackpropEvents.BACKWARD_COMPLETED)
                optimizer.step()
                engine.fire_event(BackpropEvents.OPTIM_STEP_COMPLETED)
                # ...

            trainer = Engine(update)
            trainer.register_events(*BackpropEvents)

            @trainer.on(BackpropEvents.BACKWARD_STARTED)
            def function_before_backprop(engine):
                # ...
    """

    pass


class EventsList:
    """Collection of events stacked by operator `__or__`.

    .. code-block:: python

        events = Events.STARTED | Events.COMPLETED
        events |= Events.ITERATION_STARTED(every=3)

        engine = ...

        @engine.on(events)
        def call_on_events(engine):
            # do something

    or

    .. code-block:: python

        @engine.on(Events.STARTED | Events.COMPLETED | Events.ITERATION_STARTED(every=3))
        def call_on_events(engine):
            # do something

    """

    def __init__(self) -> None:
        self._events = []  # type: List[Union[EventEnum, CallableEventWithFilter]]

    def _append(self, event: Union[EventEnum, CallableEventWithFilter]) -> None:
        if not isinstance(event, (EventEnum, CallableEventWithFilter)):
            raise TypeError(f"Argument event should be EventEnum or CallableEventWithFilter, got: {type(event)}")
        self._events.append(event)

    def __getitem__(self, item: int) -> Union[EventEnum, CallableEventWithFilter]:
        return self._events[item]

    def __iter__(self) -> Iterator[Union[EventEnum, CallableEventWithFilter]]:
        return iter(self._events)

    def __len__(self) -> int:
        return len(self._events)

    def __or__(self, other: Union[EventEnum, CallableEventWithFilter]) -> "EventsList":
        self._append(event=other)
        return self


class RemovableEventHandle:
    """A weakref handle to remove a registered event.

    A handle that may be used to remove a registered event handler via the
    remove method, with-statement, or context manager protocol. Returned from
    :meth:`~ignite.engine.engine.Engine.add_event_handler`.


    Args:
        event_name: Registered event name.
        handler: Registered event handler, stored as weakref.
        engine: Target engine, stored as weakref.

    Examples:
    .. code-block:: python

        engine = Engine()

        def print_epoch(engine):
            print(f"Epoch: {engine.state.epoch}")

        with engine.add_event_handler(Events.EPOCH_COMPLETED, print_epoch):
            # print_epoch handler registered for a single run
            engine.run(data)

        # print_epoch handler is now unregistered
    """

    def __init__(
        self,
        event_name: Union[CallableEventWithFilter, Enum, EventsList, "Events"],
        handler: Callable,
        engine: Union["Engine", "EventsDriven"],
    ) -> None:
        self.event_name = event_name
        self.handler = weakref.ref(handler)
        self.engine = weakref.ref(engine)

    def remove(self) -> None:
        """Remove handler from engine."""
        handler = self.handler()
        engine = self.engine()

        if handler is None or engine is None:
            return

        if isinstance(self.event_name, EventsList):
            for e in self.event_name:
                if engine.has_event_handler(handler, e):
                    engine.remove_event_handler(handler, e)
        else:
            if engine.has_event_handler(handler, self.event_name):
                engine.remove_event_handler(handler, self.event_name)

    def __enter__(self) -> "RemovableEventHandle":
        return self

    def __exit__(self, *args: Any, **kwargs: Any) -> None:
        self.remove()