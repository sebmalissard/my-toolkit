import asyncio
import websockets
import json
import networkx as nx
import matplotlib.pyplot as plt
import base64
from dataclasses import dataclass
import tkinter as tk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from functools import partial

@dataclass
class DeviceStruct:
    id: int = 0
    ext_addr: str = ""
    rloc16: str = ""
    best_lqi: int = 0
    best_rssi: int = 0
    available: bool = True
    is_child: bool = False
    product_name: str = ""

static_devices_info  = [
    DeviceStruct(id=0, ext_addr="12:08:14:BC:94:5E:82:A3", product_name="Open Thread Border Router"),
    DeviceStruct(id=1, ext_addr="AE:1C:B7:01:D3:7F:5E:1F", product_name="Onvis S4"),
    DeviceStruct(id=15, ext_addr="8E:B1:BF:2C:82:DD:C9:F5", product_name="Arduino Matter Device LED"),
]

devices: list[DeviceStruct] = [] 

## Funtions related to device struct

def bas64_to_eui64(b64str):
    try:
        raw = base64.b64decode(b64str)
        return ":".join(f"{b:02X}" for b in raw)
    except Exception:
        return b64str

def int_to_eui64(val):
    try:
        intval = int(val)
        return ":".join(f"{(intval >> (8 * i)) & 0xFF:02X}" for i in reversed(range(8)))
    except Exception:
        return str(val)

def int_to_rolc16(val):
    try:
        intval = int(val)
        return f"0x{intval:04X}"
    except Exception:
        return str(val)

def init_devices_from_neighbors_table(nodes):
    for node in nodes:
        attrs = node.get("attributes", {})

        # # 24-27349-006_Matter-1.4-Core-Specification-1.pdf page 841
        # "0" : ExtAddress (IEEE 64 bits address)
        # "1" : Age (seconds since last communication)
        # "2" : Rloc16 (Thread short address)
        # "3" : LinkFrameCounter
        # "4" : MleFrameCounter (Mesh Link Establishment Frame Counter)
        # "5" : LQI = Link Quality Indicator ([0,3]: 0 = non existant link, 3 = perfect link)])
        # "6" : AverageRssi (Received Signal Strength Indicator, in dBm)
        # "7" : LastRssi (in dBm)
        # "8" : FrameErrorRate (percentage)
        # "9" : MessageErrorRate (percentage)
        # "10": RxOnWhenIdle (boolean)
        # "11": FullThreadDevice (boolean, FTD=1 or MTD=0)
        # "12": FullNetworkData (boolean)
        # "13": IsChild (boolean)
        neighbors = attrs.get("0/53/7", [])
        for neighbor in neighbors:
            ext_addr_raw = neighbor.get("0")
            rloc16_raw = neighbor.get("2")
            ext_addr = int_to_eui64(ext_addr_raw)
            is_child = neighbor.get("13", False)
            if not any(dev.ext_addr == ext_addr for dev in devices):
                dev = DeviceStruct(ext_addr=ext_addr, rloc16=int_to_rolc16(rloc16_raw), is_child=is_child)
                devices.append(dev)
    
    print(f"1/ Thread devices found (number: {len(devices)}):")
    for dev in devices:
        print(f"  ext_addr={dev.ext_addr}, rloc16={dev.rloc16}")

def fill_node_id(nodes):
    for node in nodes:
        found = False
        node_id = node.get("node_id")
        attrs = node.get("attributes", {})

        # 1. Try to use Thread Diagnostic, only supported with Matter 1.3+
        # 24-27349-006_Matter-1.4-Core-Specification-1.pdf page 851
        # 0/51/63 (0x3F) => ExtAddress
        # 0/51/64 (0x40) => Rloc16
        # TODO

        # 2. Try to use Network Interface of General Diagnostic
        # Not guarentee to match, work with Eve devices
        # 24-27349-006_Matter-1.4-Core-Specification-1.pdf page 8825
        # 0/51/0 (0x00) => NetworkInterfaces
        # "0" : Name (str)
        # "1" : IsOperational (bool)
        # "2" : OffPermiseSerivesReachableIPv4 (bool)
        # "3" : OffPermiseSerivesReachableIPv6 (bool)
        # "4" : HardwareAddress (8-bit IEEE MAC Address or a 64-bit IEEE MAC Address (EUI-64))
        # "5" : IPv4Addresses (list)
        # "6" : IPv6Addresses (list)
        # "7" : Type (enum, "0" = Unspecified, "1" = WiFi, "2" = Ethernet, "3" = Cellular, "4" = Thread)
        interfaces = attrs.get("0/51/0", [])
        for interface in interfaces:
            type = interface.get("7")
            if type == 4:
                hw_addr_raw = interface.get("4")
                hw_addr = bas64_to_eui64(hw_addr_raw)
                for dev in devices:
                    if dev.ext_addr == hw_addr:
                        found = True
                        dev.id = node_id
                        dev.product_name = attrs.get("0/40/3", "")
                        break
        if found:
            continue
        
    # 3. For imcomplete devices, use static device info
    for dev in devices:
        if dev.id == 0:
            for static_dev in static_devices_info:
                if dev.ext_addr == static_dev.ext_addr:
                    dev.id = static_dev.id
                    dev.product_name = static_dev.product_name
                    break

    # Reorder devices by id
    devices.sort(key=lambda dev: dev.id)

    print(f"2/ Completed with Matter info (number: {len(devices)}):")
    for dev in devices:
        print(f"  id={dev.id:02}, ext_addr={dev.ext_addr}, rloc16={dev.rloc16}, product_name=\"{dev.product_name}\"")

def fill_info(nodes):
    for node in nodes:
        node_id = node.get("node_id")
        available = node.get("available", False)
        attrs = node.get("attributes", {})
        for dev in devices:
            if dev.id == node_id:
                dev.available = available
                neighbors = attrs.get("0/53/7", [])
                for neighbor in neighbors:
                    lqi = neighbor.get("5", 0)
                    rssi = neighbor.get("7", 0)
                    if lqi > dev.best_lqi:
                        dev.best_lqi = lqi
                        dev.best_rssi = rssi
                    elif lqi == dev.best_lqi and rssi > dev.best_rssi:
                        dev.best_rssi = rssi

                break
    
    print(f"3/ Completed with all info (number: {len(devices)}):")
    for dev in devices:
        print(f"  id={dev.id:02}, ext_addr={dev.ext_addr}, rloc16={dev.rloc16}, best_lqi={dev.best_lqi}, best_rssi={dev.best_rssi}, available={dev.available}, product_name=\"{dev.product_name}\"")

## Funtions related to graphic plot

def color_from_lqi(lqi):
    match lqi:
        case 1:
            return "red"
        case 2:
            return "orange"
        case 3:
            return "green"
        case _:
            return "skyblue"

def color_from_rssi(rssi):
    if rssi >= -60:
        return "green"
    elif rssi >= -75:
        return "orange"
    elif rssi >= -90:
        return "red"
    else:
        return "purple"

def width_from_lqi(lqi):
    match lqi:
        case 1:
            return 1
        case 2:
            return 3
        case 3:
            return 5
        case _:
            return 1

def plot_thread_topology(nodes, fig, canvas):
    G = nx.Graph()

    for dev in devices:
        if not dev.available:
            color="grey"
        else:
            if dev.is_child:
                color="lightgreen"
            else:
                color="skyblue"
        G.add_node(dev.ext_addr, color=color, label=f"{dev.id}\n{dev.product_name}")
    
    for node in nodes:
        node_id = node.get("node_id")

        for dev in devices:
            if dev.id == node_id:
                device = dev
                break

        if not device:
            print(f"Node {node_id} not found in devices list, skipping...")
            continue

        if device.is_child or not device.available:
            continue

        attrs = node.get("attributes", {})
        neighbors = attrs.get("0/53/7", [])

        for n in neighbors:
            neighbor_ext_addr_raw = n.get("0")
            neighbor_ext_addr = int_to_eui64(neighbor_ext_addr_raw)
            rssi = n.get("7", -100)
            lqi = n.get("5", 0)
            G.add_edge(device.ext_addr, neighbor_ext_addr, color=color_from_rssi(rssi), width=width_from_lqi(lqi))

    # Plot
    fig.clear()
    ax = fig.add_subplot(111)

    plt.figure(figsize=(10, 6))
    pos = nx.spring_layout(G, k=1.1, seed=4)
    node_labels = {n: G.nodes[n]['label'] for n in G.nodes}
    node_colors = [G.nodes[n]['color'] for n in G.nodes]
    edge_colors = [G.edges[n]['color'] for n in G.edges]
    edge_width = [G.edges[n]['width'] for n in G.edges]
    
    nx.draw(G, pos, with_labels=True, edge_color=edge_colors, width=edge_width, node_color=node_colors, labels=node_labels, node_size=3000, font_size=10)
    nx.draw(G, pos, with_labels=True, edge_color=edge_colors, width=edge_width, node_color=node_colors, labels=node_labels, node_size=3000, font_size=10, ax=ax)

    # Save image
    plt.title("Thread Topology")
    plt.tight_layout()
    plt.savefig("thread_topology.png")

    # Screen plot
    fig.tight_layout()
    canvas.draw()

def on_refresh(fig, canvas):
    print("Refreshing graph...")
    nodes = update_devices_info()
    plot_thread_topology(nodes, fig, canvas)

## Funtions related to get data from server

async def get_nodes():
    uri = "ws://192.168.1.2:5580/ws"
    message_id = "1"
    async with websockets.connect(uri) as websocket:
        await websocket.send(json.dumps({"message_id": message_id, "command": "get_nodes"}))
        while True:
            response = await websocket.recv()
            try:
                data = json.loads(response)
                #print("JSON message received :", data)
                if data.get("message_id") == message_id and "result" in data:
                    nodes = data.get("result")
                    if isinstance(nodes, list):
                        with open("nodes.json", "w") as f:
                            json.dump(nodes, f, indent=2)
                        return nodes
            except json.JSONDecodeError:
                print("Not JSON message:", response)

def update_devices_info():
    nodes = asyncio.run(get_nodes())
    init_devices_from_neighbors_table(nodes)
    fill_node_id(nodes)
    fill_info(nodes)
    return nodes

## Main

if __name__ == "__main__":
    nodes = update_devices_info()

    root = tk.Tk()
    root.title("Network Graph")

    fig = plt.figure()
    canvas = FigureCanvasTkAgg(fig, master=root)
    canvas.get_tk_widget().pack()

    button = tk.Button(root, text="Refresh", command=partial(on_refresh, fig, canvas))
    button.pack()

    plot_thread_topology(nodes, fig, canvas)
    root.mainloop()
