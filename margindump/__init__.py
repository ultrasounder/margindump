"""margindump - honest memory/PCIe margin probe + eye dump for a Linux PC.

Three questions, answered per link, never conflated:
  1. Is a *physical* margin measurement reachable from the OS on this box?
     (PCIe Lane Margining at Receiver, Intel RMT via ACPI BDAT)
  2. If not, what functional stress margin can we measure instead?
  3. What does the eye look like - measured where we can, model where we can't,
     and labelled as such on every plot.
"""
__version__ = "0.1.0"
