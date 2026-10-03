# schedule-scan

How a Niagara control schedule decides its value and when it next changes - the
90-day horizon, the calendar it computes boundaries in, and the single serial
queue every schedule on the station shares. Read out of `schedule-rt.jar` rather
than out of the documentation.

One Python file, standard library only.

```
./schedule-scan.py [NIAGARA_HOME]
```

## Why this exists

A control schedule looks like the simplest object in a station, and three of its
properties are worth knowing before a site asks why an output did not change.

`scanLimit` defaults to 90 days. It is the window `nextCov` searches, so a
change further out than 90 days is reported as **no next change at all** - a
null time, not a far-off one. Anything a graphic or a logic block drives off
"when does this next change" inherits that horizon.

`nextCov` returns the next change of *value*, not the next boundary. It compares
each candidate's output with `equivalent()` against the value already in effect
and steps over any boundary whose value matches, so two back-to-back periods
resolving to the same value are not a change.

And the clock is a calendar: `Chronometer` extends `java.util.GregorianCalendar`,
so every boundary resolves through that calendar's wall-clock, daylight-saving
and timezone rules. A 06:00 edge is 06:00 local on both sides of a DST change,
not a fixed number of hours apart. `clockChanged` re-runs `execute()` on any
clock step, so an NTP correction re-evaluates the output immediately rather than
waiting for the next boundary.

The one that scales badly: every control schedule on the station shares one
static `ExecutionQueue` named `Schedule:Execution`, built with `maxThreads=1`.
Its `maxQueueSize` is `0`, and `enqueue()` reads a non-positive `maxQueueSize`
as no cap - so the shared queue is unbounded and strictly serial. Every
schedule-driven write on the station runs one at a time on that one thread.

## What it reads, and where from

- `com.tridium.schedule.Chronometer` - its superclass, and the two forward-search
  horizon constants as exact millisecond values.
- `javax.baja.schedule.BControlSchedule` - what `scanLimit` actually defaults to,
  and the bytecode of `nextCov` and `clockChanged`.
- The static `ExecutionQueue` field and its constructor arguments, plus
  `enqueue`'s test of `maxQueueSize`, so "uncapped" is read rather than assumed.

## Read first: what it does and does not touch

**It never connects to a station.** It unzips `modules/schedule-rt.jar` and runs
`javap`. Nothing is installed, patched, written to a station or sent anywhere.

It needs a Niagara installation to read and a `javap` from a JDK 8. It looks for
`javap` in `$JAVAP`, then on `PATH`, then under `$JAVA_HOME` and the Niagara
install, then in Debian's default location. The install to read comes from the
first argument or `$NIAGARA_HOME`.

**The output below was measured against Niagara 4.15.5.22.** Another version may
differ, and that is the point - run it against yours rather than trusting this
page.

## Running it

```
$ ./schedule-scan.py
How a control schedule picks its value and its next change
======================================================================

read from /opt/Niagara/Niagara-4.15.5.22
javap:    1.8.0_504
source:   modules/schedule-rt.jar

The schedule clock is a calendar, not a UTC counter
----------------------------------------------------------
  com.tridium.schedule.Chronometer extends java.util.GregorianCalendar.
  Every boundary is resolved through that calendar's own wall-clock,
  daylight-saving and timezone rules - a 06:00 edge is 06:00 local on
  both sides of a DST change, not a fixed number of hours apart.

Two forward-search horizons are defined, exact to the millisecond
----------------------------------------------------------
  _90_DAYS   =    7776000000 ms  =  90 days
  _365_DAYS  =   31536000000 ms  = 365 days

How far ahead it looks: scanLimit, default 90 days
----------------------------------------------------------
  BControlSchedule.scanLimit defaults to Chronometer._90_DAYS (the
  90-day horizon above). It is the window nextCov searches for the
  next change of output; a change further out than scanLimit is
  reported as no next change at all (a null time).

nextCov returns the next change of VALUE, not the next boundary
----------------------------------------------------------
  nextCov(now) reads getOutput(now), walks nextEvent forward, and
  compares each candidate's getOutput with equivalent() against the
  value already in effect - returning the first boundary whose value
  actually differs. Two back-to-back periods that resolve to the same
  value are not a change of value; nextCov steps over them. It stops
  at now.add(getScanLimit()) - the 90-day window above.

A clock step re-evaluates the output at once
----------------------------------------------------------
  clockChanged(delta) calls super.clockChanged then execute() straight
  away, so an NTP correction or a DST shift re-runs the schedule and
  re-posts the output immediately, rather than waiting for the next
  scheduled boundary to come round.

Every control schedule shares one serial, uncapped execution queue
----------------------------------------------------------
  BControlSchedule holds a single static ExecutionQueue, named
  "Schedule:Execution", built with maxThreads=1 (one worker) and
  maxQueueSize=0. enqueue() tests maxQueueSize and, when it is <= 0,
  skips the QueueFull cap entirely - so the shared queue is unbounded
  and strictly serial. Every schedule-driven write on the station,
  from every control schedule, runs one at a time on that one thread.

======================================================================
Measured, not guessed: every number and name above is read from
schedule-rt.jar by this script and re-read identically on each run.
```

## The same finding, written up

The 90-day horizon, the value-not-boundary rule in `nextCov`, and the calendar every boundary resolves through are also written up as a page: <https://plantroomlabs.com/tools/schedule-scan/>. It carries a captured run of this program, the download with its byte count and SHA-256, the Niagara version the bytecode was read on beside the version of the JACE it was checked against, and the note on schedules and special events that explains what a graphic driven off "when does this next change" inherits.

## Licence

MIT. Written by Usama Iqbal at [Plantroom Labs](https://plantroomlabs.com).
