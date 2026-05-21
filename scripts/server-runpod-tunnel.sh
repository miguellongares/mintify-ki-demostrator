#!/usr/bin/env bash

#Only for the usage of tmux in the server. 

while true; do
    bash /home/mintify/mintify-ki-demonstrator/scripts/init_models.sh
    echo "Tunnel exited, restarting in 10s..."
    sleep 10
done