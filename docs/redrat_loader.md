# RedRat dataset loader

The `RedRatDatasetLoader` class is defined in gcirdb.py. It decodes redrat keyset XML files (REDRAT_KEYMANAGER.xml) so they can be
used to drive global cache devices. Here's an overview of how that works.

### What we need to send command to global cache device:

The `sendir` command which is sent to the global cache device via the TCP API looks like this:
```python
f'sendir,{module}:{port},{id_},{freq},{repeat},{offset},{durations}'
```
Where:
* `module` = IR module number (always 1 for iTach)
* `port` = IR Port number on module
* `id` = A identification number for verification in completeir response; also used to extend IR with continuous repeats
* `freq` = Modulation frequency (e.g., 38000 for 38khz modulation)
* `repeat` = Number of repeats to send
* `offset` = offset into the durations list for when the repeat sequence begins
* `durations` = list of durations, expressed as integer number of modulation cycles
Example string:
```
sendir,1:3,2796,38000,1,37,8,34,8,65,8,29,8,107,8,50,8,50,8,44,8,102,8,494,8,34,8,76,8,29,8,29,8,39,8,50,8,29,8,29,8,3040,8,34,8,65,8,29,8,107,8,50,8,50,8,44,8,102,8,494,8,34,8,34,8,70,8,29,8,39,8,50,8,29,8,29,8,3040
```


### Structure of REDRAT_KEYMANAGER.xml

The key manager xml file contains a number of `AVDevice` entries:
```xml
   <AVDevice>
      <Name>PC_REMOTE</Name>
      <Manufacturer>Comcast</Manufacturer>
       ...
```

Each AVDevice defines a number of `IRPacket` signals
```xml
    <Signals>
        ...
        <IRPacket xsi:type="ProntoModulatedSignal">
          <Name>7</Name>
          <UID>wAkLHDcAaEC+ohW+c5pDwA==</UID>
          <PauseRepeatMode>ConstantGap</PauseRepeatMode>
          <RepeatPause>0</RepeatPause>
          <ModulationFreq>37449.142857142855</ModulationFreq>
          <Lengths>
            <double>0.240325927734375</double>
            <double>1.789093017578125</double>
            <double>0.774383544921875</double>
          </Lengths>
          <SigData>AAEAAQABAAIAAgACAAEAAgACAAEAAQABAAEAAgACAAIAfwABAAEAAQACAAIAAgABAAIAAgABAAEAAQABAAIAAgACAH8=</SigData>
          <NoRepeats>2</NoRepeats>
          <IntraSigPause>20.98846435546875</IntraSigPause>
          <ToggleData />
          <ProntoData>0000 0070 0000 0011 0009 0043 0009 0043 0009 0043 0009 001D 0009 001D 0009 001D 0009 0043 0009 001D 0009 001D 0009 0043 0009 0043 0009 0043 0009 0043 0009 001D 0009 001D 0009 001D 0009 0312</ProntoData>
          <LengthFuzzValue>0.05</LengthFuzzValue>
          <MinPauseLength>10</MinPauseLength>
        </IRPacket>   
```

The important fields are:
* `Name`
* `ModulationFreq`
* `Lengths`
* `SigData`
* `NoRepeats`
* `IntraSigPause`

### Decoding the xml file

First, let's consider `SigData` as shown above:
```xml
<SigData>AAEACAACABAABQAFAAQADwARAAEACQABAAIAAQAIAAEAAQB/AAEACAACABAABQAFAAQADwARAAEAAQAJAAIAAQAIAAEAAQB/</SigData>
```

This is a base64 encoded string representing a list of byte sized uints. It can be decoded like:
```python
>>> print(list(base64.b64decode(sig_data)))
[0, 1, 0, 8, 0, 2, 0, 16, 0, 5, 0, 5, 0, 4, 0, 15, 0, 17, 0, 1, 0, 9, 0, 1, 0, 2, 0, 1, 0, 8, 0, 1, 0, 1, 0, 127, 0, 1, 0, 8, 0, 2, 0, 16, 0, 5, 0, 5, 0, 4, 0, 15, 0, 17, 0, 1, 0, 1, 0, 9, 0, 2, 0, 1, 0, 8, 0, 1, 0, 1, 0, 127]
```

Note that this has two values `127`, one at middle to represent the start of the repeat sequence, and again at end to mark the end
of the IR signal data. Splitting the list by this marker value, we get the following two lists:
```python
[0, 1, 0, 8, 0, 2, 0, 16, 0, 5, 0, 5, 0, 4, 0, 15, 0, 17, 0, 1, 0, 9, 0, 1, 0, 2, 0, 1, 0, 8, 0, 1, 0, 1, 0]
[0, 1, 0, 8, 0, 2, 0, 16, 0, 5, 0, 5, 0, 4, 0, 15, 0, 17, 0, 1, 0, 1, 0, 9, 0, 2, 0, 1, 0, 8, 0, 1, 0, 1, 0]
```
(Note: The two sequences above are similar, but not the same)

The first sequence, we'll call the `base sequence`. The second, we'll call the `repeat sequence`.

In both cases, the lists simply map to the lengths array (IRPacket `Lengths` tag), where each
length is a duration in milliseconds.
```python
>>>  print([lengths[i] for i in sig_data_0])
[0.204, 0.758, 0.204, 1.717, 0.204, 0.895, 0.204, 2.813, 0.204, 1.306, 0.204, 1.306, 0.204, 1.169, 0.204, 2.676, 0.204, 13.0, 0.204, 0.758, 0.204, 1.854, 0.204, 0.758, 0.204, 0.895, 0.204, 0.758, 0.204, 1.717, 0.204, 0.758, 0.204, 0.758, 0.204]
>>>  print([lengths[i] for i in sig_data_1])
[0.204, 0.758, 0.204, 1.717, 0.204, 0.895, 0.204, 2.813, 0.204, 1.306, 0.204, 1.306, 0.204, 1.169, 0.204, 2.676, 0.204, 13.0, 0.204, 0.758, 0.204, 0.758, 0.204, 1.854, 0.204, 0.895, 0.204, 0.758, 0.204, 1.717, 0.204, 0.758, 0.204, 0.758, 0.204]
```

Note that the odd indexed values of both sequences are pulse durations (how long IR is on at modulation frequency),
while the even indexed values of both sequences are space durations (how long IR is off between pulses) 

Now that we have decoded the base sequence and repeat sequence, we can construct an IR signal simply by playing the
base sequence followed by some number of repeats (defined by the IRPacket `NoRepeats` value), with an extra space duration
of the IRPacket `IntraSigPause` inserted between sequences (before every repeat sequence is played)

However, note that the global cache devices require durations represented in units of modulation
cycle time (1 / modulation_freq). We can get the number of cycles approximately like this:
```python
>>> modulation_freq = 38000 # from IRPacket
>>> base_time = [lengths[i] for i in sig_data_0]
>>> base_sequence = [v * modulation_freq / 1000 for v in base_time]
>>> base_sequence_quantized = [round(v) for v in base_sequence]
[8, 29, 8, 65, 8, 34, 8, 107, 8, 50, 8, 50, 8, 44, 8, 102, 8, 494, 8, 29, 8, 70, 8, 29, 8, 34, 8, 29, 8, 65, 8, 29, 8, 29, 8]
```

Note that in gcirdb.py, the method to quantize the modulation cycles to integers is slightly more complex
than just calling `round` on each value (as shown above). Instead, it works by accumulating error so that the total sequence duration
most closely matches the expected duration based on simply adding up the individual lengths in the sequence.

Now that we have the base sequence and repeat sequence represented in modulation cycles, we
are able to call the global cache TCP socket based APIs. Refer to: https://www.globalcache.com/files/docs/API-GC-UnifiedTCPv1.1.pdf

One additional operation that occurs in gcirdb.py when loading redrat keyset files is we calculate
sequence durations again, but based on modulation frequency and cycles. This is used for calculations involving durations for
the press_key API (when durations are passed in, as opposed to number of repeats).