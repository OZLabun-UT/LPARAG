
3/8/26

- Fixed EC2 instance and updated the newest commit onto it. It's working again now with all the updates

- Added some website features like quick clear all chats, and the s3 bucket link on the images in case the returned reference doesn't work. 

3/7/26

- Fixed relevance scores, it had placeholder code I forgot I added that was setting the score to 0 manually.

- Had to resync the lpa-not-simuilation because it was giving me issues pointing to files in the wrong directory. This is probably because of the rebalancing script that makes sure the file size limit is not reached in each bucket. 

3/6/26

- Continued syncing knowledge bases
    - The LWFA Simulation KB is already synced so starting on the other ones

- Tested Simulation KB to see if everything still works. The only thing broken is the relevance scores and im not sure why so I'll try to fix. 

- After the AWS credits expired, the EC2 instance was killed so I need to restart it which is usually a time consuming process but I made sure to write down the exact steps to setup a new instance last time I did this just in case something like this happened.

