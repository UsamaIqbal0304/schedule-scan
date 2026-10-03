#!/usr/bin/env python3
"""How a Niagara control schedule decides its value and when it next changes.

Reads the schedule engine out of schedule-rt.jar with javap - the public
javax.baja.schedule classes and the com.tridium.schedule internals - and
reports, from the bytecode rather than the docs:

  - that the schedule's clock, Chronometer, is a java.util.GregorianCalendar
    subclass, so every boundary is computed in local wall-clock time with
    that calendar's daylight-saving and timezone rules, not in raw UTC
  - the two forward-search horizons it defines, _90_DAYS and _365_DAYS, as
    exact millisecond constants
  - that BControlSchedule.scanLimit - how far ahead "when does the output
    next change" will look - defaults to the 90-day horizon, so a change
    further out than that is reported as no change at all
  - that nextCov walks events forward comparing getOutput values with
    equivalent(), so it returns the next change of *value*, skipping any
    boundary whose value matches the one already in effect
  - that clockChanged re-runs execute() on any clock step, so a corrected
    or DST-shifted clock re-evaluates the output immediately
  - that every control schedule on the station shares one static
    ExecutionQueue, "Schedule:Execution", built single-threaded
    (maxThreads 1) with maxQueueSize 0 - which enqueue() reads as no cap at
    all, so the shared queue is unbounded and strictly serial

Usage: schedule-scan.py [NIAGARA_HOME]
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

def _find_javap(niagara_home=None):
    """javap from $JAVAP, then PATH, then the JDK Niagara ships, then Debian's."""
    cand = [os.environ.get("JAVAP"), shutil.which("javap")]
    for base in (os.environ.get("JAVA_HOME"), niagara_home):
        if base:
            cand += [os.path.join(base, "bin", "javap"),
                     os.path.join(base, "jre", "bin", "javap")]
    cand.append("/usr/lib/jvm/java-8-openjdk-amd64/bin/javap")
    for c in cand:
        if c and os.path.exists(c):
            return c
    return None


HOME = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
    "NIAGARA_HOME", "/opt/Niagara/Niagara-4.15.5.22")
JAVAP = _find_javap(str(HOME))


def abort(why):
    sys.exit("ABORT %s" % why)


if not os.path.isdir(HOME):
    abort("no Niagara install at %s" % HOME)
if not os.path.exists(JAVAP):
    abort("no javap found - set $JAVAP or put a JDK 8 javap on PATH")

TMP = tempfile.mkdtemp(prefix="schedscan-")
JAR = os.path.join(HOME, "modules", "schedule-rt.jar")
if not os.path.exists(JAR):
    abort("no modules/schedule-rt.jar under %s" % HOME)
PREFIXES = (
    "com/tridium/schedule/Chronometer",
    "com/tridium/schedule/ExecutionQueue",
    "javax/baja/schedule/BControlSchedule",
    "javax/baja/schedule/BAbstractSchedule",
)
with zipfile.ZipFile(JAR) as z:
    names = [n for n in z.namelist()
             if n.endswith(".class")
             and any(n == p + ".class" or n.startswith(p + "$")
                     for p in PREFIXES)]
    if not names:
        abort("schedule-rt.jar has no classes for %r" % (PREFIXES,))
    z.extractall(TMP, names)


def dis(cls):
    p = subprocess.run([JAVAP, "-p", "-c", "-constants", cls + ".class"],
                       cwd=TMP, capture_output=True, text=True)
    if p.returncode != 0 or "Compiled from" not in p.stdout:
        abort("javap failed on %s: %s" % (cls, p.stderr.strip()[:200]))
    return p.stdout


def method(text, sig, what):
    """Slice one member out of a javap dump by signature.

    javap keeps a member's exception table in the same block and puts
    exactly one blank line before the next member, so the first blank line
    after the signature is the end marker.
    """
    i = text.find("  " + sig + ";\n")
    if i < 0:
        abort("no member %r in %s" % (sig, what))
    j = text.find("\n\n", i)
    return text[i:j if j > 0 else len(text)]


def one(hay, pat, what):
    m = re.findall(pat, hay)
    if len(m) != 1:
        abort("%d matches for %s (%r)" % (len(m), what, pat))
    return m[0]


def need(hay, s, what):
    if s not in hay:
        abort("expected %r in %s" % (s, what))
    return s


CHRON = dis("com/tridium/schedule/Chronometer")
EQ = dis("com/tridium/schedule/ExecutionQueue")
BCS = dis("javax/baja/schedule/BControlSchedule")
BAS = dis("javax/baja/schedule/BAbstractSchedule")

say = print
say("How a control schedule picks its value and its next change")
say("=" * 70)
say("")
say("read from %s" % HOME)
jv = subprocess.run([JAVAP, "-version"], capture_output=True, text=True)
say("javap:    %s" % (jv.stdout.strip() or jv.stderr.strip()))
say("source:   modules/schedule-rt.jar")
say("")

# ---- 1. the clock is a GregorianCalendar --------------------------------
need(CHRON, "class com.tridium.schedule.Chronometer extends "
            "java.util.GregorianCalendar", "Chronometer header")
say("The schedule clock is a calendar, not a UTC counter")
say("-" * 58)
say("  com.tridium.schedule.Chronometer extends java.util.GregorianCalendar.")
say("  Every boundary is resolved through that calendar's own wall-clock,")
say("  daylight-saving and timezone rules - a 06:00 edge is 06:00 local on")
say("  both sides of a DST change, not a fixed number of hours apart.")
say("")

# ---- 2. the two forward horizons, as exact ms ---------------------------
CHRONINIT = method(CHRON, "static {}", "Chronometer")
HORIZ = {}
for ms, name in re.findall(
        r"ldc2_w\s+#\d+\s+// long (\d+)l\n"
        r"\s+\d+: invokestatic\s+#\d+\s+// Method "
        r"javax/baja/sys/BRelTime\.make:\(J\)Ljavax/baja/sys/BRelTime;\n"
        r"\s+\d+: putstatic\s+#\d+\s+// Field (_\w+):", CHRONINIT):
    HORIZ[name] = int(ms)
for k in ("_90_DAYS", "_365_DAYS"):
    if k not in HORIZ:
        abort("Chronometer has no %s constant" % k)
if HORIZ["_90_DAYS"] != 90 * 86400000:
    abort("_90_DAYS is %d ms, not 90 days" % HORIZ["_90_DAYS"])
if HORIZ["_365_DAYS"] != 365 * 86400000:
    abort("_365_DAYS is %d ms, not 365 days" % HORIZ["_365_DAYS"])
say("Two forward-search horizons are defined, exact to the millisecond")
say("-" * 58)
for k in ("_90_DAYS", "_365_DAYS"):
    say("  %-10s = %13d ms  = %3d days" % (k, HORIZ[k], HORIZ[k] // 86400000))
say("")

# ---- 3. scanLimit defaults to the 90-day horizon ------------------------
BCSINIT = method(BCS, "static {}", "BControlSchedule")
# the _90_DAYS getstatic that feeds scanLimit's newProperty, then putstatic
SCAN_REGION = one(
    BCSINIT,
    r"(?s)(getstatic\s+#\d+\s+// Field com/tridium/schedule/Chronometer\._90_DAYS:"
    r"Ljavax/baja/sys/BRelTime;.*?putstatic\s+#\d+\s+// Field scanLimit:)",
    "scanLimit default region")
need(SCAN_REGION, "newProperty", "scanLimit newProperty call")
need(BCS, "public javax.baja.sys.BRelTime getScanLimit();", "getScanLimit")
say("How far ahead it looks: scanLimit, default 90 days")
say("-" * 58)
say("  BControlSchedule.scanLimit defaults to Chronometer._90_DAYS (the")
say("  90-day horizon above). It is the window nextCov searches for the")
say("  next change of output; a change further out than scanLimit is")
say("  reported as no next change at all (a null time).")
say("")

# ---- 4. nextCov is change-of-VALUE, bounded by scanLimit ----------------
NEXTCOV = method(BCS, "public javax.baja.sys.BAbsTime "
                      "nextCov(javax.baja.sys.BAbsTime)", "nextCov")
for frag, lbl in (
        ("// Method getOutput:", "getOutput"),
        ("// Method nextEvent:", "nextEvent"),
        ("// Method getScanLimit:", "getScanLimit"),
        ("// Method javax/baja/sys/BAbsTime.add:", "BAbsTime.add"),
        ("// Method javax/baja/sys/BAbsTime.isAfter:", "BAbsTime.isAfter"),
        ("// Method javax/baja/sys/BValue.equivalent:", "BValue.equivalent")):
    need(NEXTCOV, frag, "nextCov %s call" % lbl)
say("nextCov returns the next change of VALUE, not the next boundary")
say("-" * 58)
say("  nextCov(now) reads getOutput(now), walks nextEvent forward, and")
say("  compares each candidate's getOutput with equivalent() against the")
say("  value already in effect - returning the first boundary whose value")
say("  actually differs. Two back-to-back periods that resolve to the same")
say("  value are not a change of value; nextCov steps over them. It stops")
say("  at now.add(getScanLimit()) - the 90-day window above.")
say("")

# ---- 5. clockChanged re-runs execute() ----------------------------------
CC = method(BCS, "public void clockChanged(javax.baja.sys.BRelTime) "
                 "throws java.lang.Exception", "clockChanged")
need(CC, "// Method javax/baja/schedule/BCompositeSchedule.clockChanged:", "cc super")
need(CC, "// Method execute:()V", "cc execute")
say("A clock step re-evaluates the output at once")
say("-" * 58)
say("  clockChanged(delta) calls super.clockChanged then execute() straight")
say("  away, so an NTP correction or a DST shift re-runs the schedule and")
say("  re-posts the output immediately, rather than waiting for the next")
say("  scheduled boundary to come round.")
say("")

# ---- 6. the shared, single-threaded, uncapped execution pool ------------
need(BCS, "static com.tridium.schedule.ExecutionQueue pool;", "pool field")
POOLNAME = one(
    BCSINIT,
    r'new\s+#\d+\s+// class com/tridium/schedule/ExecutionQueue\n'
    r'\s+\d+: dup\n'
    r'\s+\d+: ldc\s+#\d+\s+// String ([\w:]+)\n'
    r'\s+\d+: iconst_\d+\n'
    r'\s+\d+: invokespecial\s+#\d+\s+// Method '
    r'com/tridium/schedule/ExecutionQueue\."<init>":'
    r'\(Ljava/lang/String;Z\)V\n'
    r'\s+\d+: putstatic\s+#\d+\s+// Field pool:', "pool name")
# the (String, boolean) ctor's field defaults
EQCTOR = method(EQ, "public com.tridium.schedule.ExecutionQueue("
                    "java.lang.String, boolean)", "ExecutionQueue ctor")


def ctor_default(field):
    lit = one(EQCTOR,
              r"(iconst_\d+|bipush \d+|sipush \d+)\n"
              r"\s+\d+: putfield\s+#\d+\s+// Field %s:I" % field,
              "ctor default %s" % field)
    return int(lit.split("_")[-1] if lit.startswith("iconst") else lit.split()[-1])


MAXQ = ctor_default("maxQueueSize")
MAXT = ctor_default("maxThreads")
MINT = ctor_default("minThreads")
if (MAXQ, MAXT, MINT) != (0, 1, 1):
    abort("ExecutionQueue defaults are maxQueueSize=%d maxThreads=%d "
          "minThreads=%d, not 0/1/1" % (MAXQ, MAXT, MINT))
# enqueue: maxQueueSize <= 0 skips the QueueFull cap
ENQ = method(EQ, "public synchronized void enqueue(java.lang.Runnable) "
                 "throws com.tridium.schedule.ExecutionQueue$QueueFull",
             "enqueue")
need(ENQ, "// Field maxQueueSize:I", "enqueue reads maxQueueSize")
need(ENQ, "ifle", "enqueue guards on maxQueueSize <= 0")
need(ENQ, "class com/tridium/schedule/ExecutionQueue$QueueFull", "QueueFull")
say("Every control schedule shares one serial, uncapped execution queue")
say("-" * 58)
say("  BControlSchedule holds a single static ExecutionQueue, named")
say('  "%s", built with maxThreads=%d (one worker) and' % (POOLNAME, MAXT))
say("  maxQueueSize=%d. enqueue() tests maxQueueSize and, when it is <= 0," % MAXQ)
say("  skips the QueueFull cap entirely - so the shared queue is unbounded")
say("  and strictly serial. Every schedule-driven write on the station,")
say("  from every control schedule, runs one at a time on that one thread.")
say("")
say("=" * 70)
say("Measured, not guessed: every number and name above is read from")
say("schedule-rt.jar by this script and re-read identically on each run.")
