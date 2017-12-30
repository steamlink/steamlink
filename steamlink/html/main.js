/******************************************************
                 DESCRIPTIONS
*******************************************************

---------------------------------
|Room Type    |Room Name        |
---------------------------------
|home_id      |Steam_<ID>       |
---------------------------------
|mesh_all     |Mesh_*           |
|mesh_id      |Mesh_<ID>        |
|mesh_id_all  |Mesh_<ID>_*      |
|             |                 |
---------------------------------
|node_all     |Node_*           |
|node_id      |Node_<ID>        |
|node_id_all  |Node_<ID>_*      |
|             |                 |
---------------------------------
|pkt_all      |Pkt_*            |
|pkt_id       |Pkt_<ID>         |
|pkt_id_all   |Pkt_<ID>_*       |
|             |                 |
---------------------------------

---------------------------------
|Tile Types:                    |
---------------------------------
|mesh                           |
|node                           |
|pkt                            |
---------------------------------

---------------------------------
|Header Types:                  |
---------------------------------
|home                           |
|mesh                           |
|node                           |
---------------------------------

*****************************************************/

//////////////////////////////////////////////////////

/******************************************************
              GLOBALS
*******************************************************/

/* CONSTANTS */
var socketNamespace = '/sl';
var socketUrl = 'http://' + document.domain + ':' + location.port;

var socket;
var vueApp;

/* ARRAY MANIPULATION FOR TILES */
function findIndexById(tiles, id) {
    return tiles.findIndex(function(tile){
        return tile.id === id;
    });
}

function insertTile(tiles, tile) {
    console.log("pushing tile")
    tiles.push(tile);
}

function removeTile(tiles, id) {
    var index = findIndexById(tiles, id);
    if (index > -1) {
        tiles.splice(index, 1);
    }
}

function updateTile(tiles, tile) {
    var index = findIndexById(tiles, tile.id);
    if (index > -1) {
        tiles.splice(index, 1, tile);
    } else {
        insertTile(tiles, tile);
    }
}

/* SOCKETIO HELPER FUNCTIONS */

function joinRoom(skt, room) {
    console.log("Joining room: ", room);
    skt.emit('join', {"room" : room});     
}

function leaveRoom(skt, room) {
    console.log("Leaving room: ", room);
    skt.emit('leave', {"room" : room});     
}

/****************************************************
                 VUE COMPONENTS
*****************************************************/

/*
Component name: tileitem
Props: display_vals, id
TODO: 
1. need to include tileType as prop
2. Change display_vals to displayVals
*/

Vue.component('tileitem', {
    template: "#tiletemplate",
    props : ['display_vals', 'id', 'tileType'],
    methods: {
        clicked: function() {
            console.log("selected " + this.id);
            this.$emit('select', this.id
                       /*
                         {"tileId": this.id, "tileType" : this.tileType}
                       */
                       );
        }
    }
})

/*
Component name: tiler
Props: tiles, tilerPerRow, tileType
TODO: need to include tileType as prop
*/

Vue.component('tiler', {
    template: "#tilertemplate",
    props: ['tiles', 'tilesPerRow', 'tileType'],
    methods: {
        filteredTilesByRow: function(row) {
            return this.tiles.slice((row-1)*this.tilesPerRow,(row*this.tilesPerRow));
        },
        select: function(id) {
            this.$emit('select', id);
        }
    },
    computed: {
        rows: function() {
            if (this.tiles) {
                return Math.ceil(this.tiles.length/this.tilesPerRow);
            } else {
                return 0;
            }
        }
    }
})

/****************************************************
                         MAIN
*****************************************************/

window.onload = function(){
    vueApp = new Vue({
        el: '#app',
        data: {
            curHeaderRoom: 'Steam_0',
            headerRoomType: 'home_id',
            curTileRoom: 'Mesh_*',
            tileRoomType: 'mesh_all',
            tiles: [],
            header: {},
            tilesPerRow: 3
        },
        methods: {
            swap: function(id) {
                Vue.set(this, "header", {});
                Vue.set(this, "tiles", []);
                
                var newTileRoom;
                var newHeaderRoom;
                var newTileRoomType;
                var newHeaderRoomType;

                console.log("in  room " + this.headerRoomType);

//                if (this.headerRoomType === "mesh_all") {
                if (this.headerRoomType === "home_id") {
                    // We want to go to a specific mesh display
                    // The header will be description for the mesh
                    // Tiles will be all the nodes
                    newTileRoom = "Mesh_" + id + "_*";
                    newTileRoomType = "mesh_id_all"
                    newHeaderRoom = "Mesh_" + id;
                    newHeaderRoomType = "mesh_id";                   
                }

                if (this.headerRoomType === "node_all") {
                    // We want to go to a specific node display
                    // The header will be description for the node
                    // Tiles will be all the pkts
                    newTileRoom = "Node_" + id + "_*";
                    newTileRoomType = "node_id_all"
                    newHeaderRoom = "Node_" + id;
                    newHeaderRoomType = "node_id";                                      
                }
                
                if (this.headerRoomType === "mesh_id") {
                    // We want to go to a specific node display
                    // The header will be description for the node
                    // Tiles will be all the pkts
                    newTileRoom = "Node_" + id + "_*";
                    newTileRoomType = "node_id_all"
                    newHeaderRoom = "Node_" + id;
                    newHeaderRoomType = "node_id";                                      
                }

                leaveRoom(socket, this.curHeaderRoom);
                leaveRoom(socket, this.curTileRoom);
                
                /*
                console.log("leaving room " + this.curHeaderRoom);
                console.log("leaving room " + this.curTileRoom);

                socket.emit('leave', {"room" : this.curHeaderRoom});
                socket.emit('leave', {"room" : this.curTileRoom});
                */
                
                this.curHeaderRoom = newHeaderRoom;
                this.curTileRoom = newTileRoom;              

                this.curHeaderRoomType = newHeaderRoomType;
                this.curTileRoomType = newTileRoomType;

                joinRoom(socket, this.curHeaderRoom);
                joinRoom(socket, this.curTileRoom);
                
                /*
                socket.emit('join', {"room" : this.curHeaderRoom});
                socket.emit('join', {"room" : this.curTileRoom});
                
                console.log("joining room " + this.curHeaderRoom);
                console.log("joining room " + this.curTileRoom);
                */
            }
        }
    })

    /* Set up socket.io */
    socket = io.connect(socketUrl + socketNamespace);

    socket.on('connect', function() {
        console.log("connected to websocket server");
        socket.emit('connected', {data: 'I\'m connected!'});
        joinRoom(socket, vueApp.curTileRoom);
        joinRoom(socket, vueApp.curHeaderRoom);
        /*
        socket.emit('join', {"room": "Steam_0"});
        socket.emit('join', {"room": "Mesh_*"});
        */
    });

    socket.on('disconnect', function() {
        console.log("dead");
    });

    socket.on('data_full', function(msg){
        console.log("Received data!");
        console.log(msg);
        if (msg.header) {
            console.log("updating header message");
            Vue.set(vueApp.header, "display_vals", msg.display_vals);
            // validate if this is for a tile or for header
        } else {
            console.log("updating tile message");
            updateTile(vueApp.tiles, msg);
        }
        
    });
}

Vue.config.devtools = true;

















/*************************************************
*********       TEST DATA     *******************
*************************************************/
/*
var resp = {
    tiles: [
        {
            display_vals : {
                name: "Swarm 1",
                description: "Somewhere nearby",
                total_pkt_sent: 10,
                total_pkt_rcv: 20
            },
            id: "swarm1"
        },
        {
            display_vals : {
                name: "Swarm 2",
                description: "Somewhere further",
                total_pkt_sent: 10,
                total_pkt_rcv: 20
            },
            id: "swarm2"
        },
        {
            display_vals : {
                name: "Swarm 3",
                description: "Somewhere far",
                total_pkt_sent: 10,
                total_pkt_rcv: 20
            },
            id: "swarm3"
        },
        {
            display_vals : {
                name: "Swarm 4",
                description: "Somewhere really far",
                total_pkt_sent: 10,
                total_pkt_rcv: 20
            },
            id: "swarm4"
        },
        {
            display_vals : {
                name: "Swarm 5",
                description: "Somewhere very far",
                total_pkt_sent: 10,
                total_pkt_rcv: 20
            },
            id: "swarm5"
        }
    ],
    tilesPerRow: 3
};

var resp1 = {
    tiles: [
        {
            display_vals : {
                name: "Node 1",
                description: "Somewhere nearby",
                total_pkt_sent: 10,
                total_pkt_rcv: 20
            },
            id: "node1"
        },
        {
            display_vals : {
                name: "Node 2",
                description: "Somewhere further",
                total_pkt_sent: 10,
                total_pkt_rcv: 20
            },
            id: "node2"
        },
        {
            display_vals : {
                name: "Node 3",
                description: "Somewhere far",
                total_pkt_sent: 10,
                total_pkt_rcv: 20
            },
            id: "node3"
        },
        {
            display_vals : {
                name: "Node 4",
                description: "Somewhere really far",
                total_pkt_sent: 10,
                total_pkt_rcv: 20
            },
            id: "node4"
        },
        {
            display_vals : {
                name: "Node 5",
                description: "Somewhere very far",
                total_pkt_sent: 10,
                total_pkt_rcv: 20
            },
            id: "node5"
        }
    ],
    tilesPerRow: 3
};



var new_swarm_tile = {
    display_vals : {
        name: "Swarm 6",
        description: "Somewhere very far",
        total_pkt_sent: 10,
        total_pkt_rcv: 20
    },
    id: "swarm 6"
}


var new_node_tile = {
    display_vals : {
        name: "Node 6",
        description: "Somewhere very far",
        total_pkt_sent: 10,
        total_pkt_rcv: 20
    },
    id: "node 6"
}
*/
