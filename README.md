# DAEN_328_GDELT_Project
This repository contains a collaborative 4 member team project working with the GDELT project API, where we will analyze the data to uncover interesting findings about the current state of the world

...

## Incremental Pulls
Each zip file extracted is labeled with the time and date that it was published. We used those timestamps as identifiers for where each extraction starts and stops. `get_lastupdate_timestamp()` returns the timestamp of the last pulled file, if it exists, and `get_starting_timestamp()` identifies the next file that needs to be pulled. `saved_timestamps()` returns the timestamps, which are used as id's, of the pulled files that have been saved. 
