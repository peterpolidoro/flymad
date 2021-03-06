import numpy as np

def myint32(val):
    val = max(-2**31+1,val)
    val = min(2**31-1,val)
    return int(val)

def myint16(val):
    val = max(-2**15+1,val)
    val = min(2**15-1,val)
    return int(val)

def dac_value_wrap(val):
    return int( np.int16(val) )

def test_wrap():
    for val1 in range(-0x3fff, 0x3fff+1, 0x1000):
        val2 = dac_value_wrap(val1)
        assert val1==val2
    for val1 in range(-0x3fff, 0x3fff+1, 0x1000):
        val2 = dac_value_wrap(float(val1))
        assert val1==val2
