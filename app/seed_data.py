"""Illustrative timetables and landmarks for the demo network.

Times and bearings are a teaching dataset, not a working railway timetable.
Each origin has trains to more than one destination, and the long routes
run back as well. Landmarks sit on the left or right of the direction of travel.
"""

from __future__ import annotations

# code, name, lat, lon
STATIONS = {
    "CSMT": ("Mumbai CSMT", 18.940, 72.835),
    "DR": ("Dadar", 19.018, 72.843),
    "TNA": ("Thane", 19.186, 72.976),
    "KYN": ("Kalyan", 19.235, 73.130),
    "KJT": ("Karjat", 18.910, 73.323),
    "LNL": ("Lonavala", 18.748, 73.407),
    "SVJR": ("Shivajinagar", 18.532, 73.852),
    "PUNE": ("Pune Jn", 18.529, 73.874),
    "PNVL": ("Panvel", 18.989, 73.122),
    "ROHA": ("Roha", 18.445, 73.119),
    "CHI": ("Chiplun", 17.533, 73.518),
    "RN": ("Ratnagiri", 16.994, 73.301),
    "KKW": ("Kankavali", 16.266, 73.712),
    "KUDL": ("Kudal", 16.011, 73.688),
    "THVM": ("Thivim", 15.592, 73.808),
    "MAO": ("Madgaon", 15.275, 73.970),
    "SBC": ("KSR Bengaluru", 12.978, 77.568),
    "MYA": ("Mandya", 12.522, 76.895),
    "MYS": ("Mysuru", 12.316, 76.638),
    "HAS": ("Hassan", 13.004, 76.102),
    "SKLR": ("Sakleshpur", 12.941, 75.785),
    "SBHR": ("Subramanya Road", 12.668, 75.640),
    "KBPR": ("Kabaka Puttur", 12.760, 75.207),
    "MAJN": ("Mangaluru Central", 12.864, 74.843),
    "MRJ": ("Miraj Jn", 16.830, 74.640),
    "BGM": ("Belagavi", 15.850, 74.504),
    "UBL": ("SSS Hubballi", 15.350, 75.137),
    "KAWR": ("Karwar", 14.808, 74.124),
    "UD": ("Udupi", 13.340, 74.742),
}

# stop: code, arr, dep, distance_km, arr_min, dep_min, bearing of the segment that leaves
DECCAN = [
    ("CSMT", None, "07:10", 0, None, 0, 150),
    ("DR", "07:18", "07:21", 9, 8, 11, 140),
    ("TNA", "07:42", "07:45", 34, 32, 35, 125),
    ("KYN", "08:08", "08:11", 54, 58, 61, 130),
    ("KJT", "09:02", "09:05", 100, 112, 115, 150),
    ("LNL", "09:55", "09:58", 128, 165, 168, 115),
    ("SVJR", "11:02", "11:04", 180, 232, 234, 100),
    ("PUNE", "11:18", None, 192, 248, None, None),
]

KONKAN = [
    ("CSMT", None, "06:40", 0, None, 0, 175),
    ("PNVL", "07:38", "07:41", 68, 58, 61, 185),
    ("ROHA", "08:52", "08:55", 143, 132, 135, 190),
    ("CHI", "11:18", "11:21", 319, 278, 281, 185),
    ("RN", "13:35", "13:40", 431, 415, 420, 180),
    ("KKW", "15:02", "15:05", 520, 502, 505, 175),
    ("KUDL", "15:48", "15:51", 578, 548, 551, 170),
    ("THVM", "17:02", "17:05", 648, 622, 625, 160),
    ("MAO", "17:50", None, 765, 670, None, None),
]

MALNAD = [
    ("SBC", None, "08:00", 0, None, 0, 250),
    ("MYA", "09:08", "09:10", 92, 68, 70, 255),
    ("MYS", "10:18", "10:28", 139, 138, 148, 270),
    ("HAS", "12:05", "12:15", 230, 245, 255, 275),
    ("SKLR", "13:45", "13:55", 290, 345, 355, 280),
    ("SBHR", "16:12", "16:16", 360, 492, 496, 275),
    ("KBPR", "17:22", "17:25", 410, 562, 565, 270),
    ("MAJN", "18:45", None, 472, 645, None, None),
]

# segment_idx, side, score, kind, description, minutes after the segment starts
DECCAN_MARKS = [
    (1, "L", 4, "harbour", "Eastern waterfront leaving Mumbai CSMT", 4),
    (2, "L", 6, "creek", "Thane creek opens on the left", 10),
    (3, "R", 5, "tunnel", "Parsik tunnel and the Mumbra hills", 8),
    (4, "R", 7, "ghat", "Matheran hills begin to climb", 18),
    (5, "L", 6, "valley", "Palasdari valley", 8),
    (5, "R", 10, "ghat", "Bhor Ghat. The escarpment is on the right", 16),
    (5, "R", 8, "waterfall", "Monsoon falls beside Monkey Hill", 28),
    (6, "L", 6, "lake", "Tungarli lake just after Lonavala", 6),
    (6, "R", 9, "peak", "Duke's Nose stands over the valley", 12),
    (6, "L", 8, "valley", "Khandala valley drops away", 20),
    (7, "L", 3, "city", "Pune approach", 8),
]

KONKAN_MARKS = [
    (1, "L", 5, "harbour", "Harbour yards leaving CSMT", 6),
    (2, "R", 6, "hill", "Karnala and the first Konkan foothills", 22),
    (3, "L", 8, "river", "Savitri river bridge", 28),
    (3, "R", 6, "tunnel", "First of the Konkan tunnels", 48),
    (4, "L", 7, "creek", "Chiplun creek", 16),
    (4, "R", 10, "sea", "Arabian Sea opens and stays on the right", 40),
    (5, "R", 9, "sea", "Ratnagiri cliffs above the water", 30),
    (5, "L", 6, "fort", "Ratnadurg headland", 55),
    (6, "R", 7, "sea", "Laterite coast, sea still on the right", 18),
    (7, "L", 5, "village", "Konkan villages set back from the line", 14),
    (8, "L", 8, "river", "Zuari river", 22),
    (8, "R", 7, "river", "Mandovi approach into Goa", 36),
]

MALNAD_MARKS = [
    (1, "L", 2, "city", "Bengaluru yard and the city edge", 15),
    (2, "R", 3, "plain", "Cane country toward Mysuru", 20),
    (3, "L", 5, "hill", "Chamundi hill on the horizon", 18),
    (4, "R", 7, "lake", "Hemavati reservoir", 28),
    (5, "R", 10, "ghat", "Sakleshpur descent, the Green Route", 20),
    (5, "L", 9, "waterfall", "Ghat waterfalls on the left", 42),
    (5, "R", 8, "tunnel", "A run of ghat tunnels", 65),
    (6, "L", 7, "forest", "Forest canopy closes over the line", 20),
    (7, "L", 8, "river", "Netravati river", 16),
    (7, "R", 6, "sea", "Coastal light on the run into Mangaluru", 35),
]

TRAINS = [
    {
        "train_id": 1,
        "train_no": "11007",
        "name": "Deccan Express",
        "route_name": "Western Ghats",
        "stops": DECCAN,
        "landmarks": DECCAN_MARKS,
    },
    {
        "train_id": 2,
        "train_no": "10111",
        "name": "Konkan Kanya Express",
        "route_name": "Konkan coast",
        "stops": KONKAN,
        "landmarks": KONKAN_MARKS,
    },
    {
        "train_id": 3,
        "train_no": "56641",
        "name": "Malnad Express",
        "route_name": "Malnad Ghats",
        "stops": MALNAD,
        "landmarks": MALNAD_MARKS,
    },
    {
        "train_id": 4,
        "train_no": "12127",
        "name": "Intercity Express",
        "route_name": "Mumbai Pune fast",
        "stops": [
            ("CSMT", None, "14:10", 0, None, 0, 150),
            ("KYN", "15:05", "15:08", 54, 55, 58, 130),
            ("LNL", "16:35", "16:38", 128, 145, 148, 115),
            ("PUNE", "17:55", None, 192, 225, None, None),
        ],
        "landmarks": [
            (2, "R", 9, "ghat", "Bhor Ghat escarpment on the afternoon run", 20),
            (3, "L", 6, "valley", "Khandala valley after Lonavala", 12),
        ],
    },
    {
        "train_id": 5,
        "train_no": "11008",
        "name": "Deccan Express",
        "route_name": "Pune Mumbai",
        "stops": [
            ("PUNE", None, "15:00", 0, None, 0, 280),
            ("SVJR", "15:12", "15:14", 12, 12, 14, 295),
            ("LNL", "16:28", "16:31", 64, 88, 91, 300),
            ("KJT", "17:18", "17:21", 92, 138, 141, 310),
            ("KYN", "18:10", "18:13", 138, 190, 193, 320),
            ("TNA", "18:32", "18:35", 158, 212, 215, 340),
            ("DR", "18:55", "18:58", 183, 235, 238, 330),
            ("CSMT", "19:15", None, 192, 255, None, None),
        ],
        "landmarks": [
            (2, "L", 8, "valley", "Khandala valley, now on the left going north", 18),
            (3, "L", 9, "ghat", "Bhor Ghat falls away on the left", 16),
        ],
    },
    {
        "train_id": 6,
        "train_no": "11020",
        "name": "Sahyadri Express",
        "route_name": "Pune Mumbai morning",
        "stops": [
            ("PUNE", None, "08:40", 0, None, 0, 280),
            ("LNL", "10:05", "10:08", 64, 85, 88, 300),
            ("KJT", "10:55", "10:58", 92, 135, 138, 310),
            ("KYN", "11:50", "11:53", 138, 190, 193, 320),
            ("CSMT", "13:35", None, 192, 295, None, None),
        ],
        "landmarks": [
            (1, "L", 8, "ghat", "Morning ghats on the left after Lonavala", 30),
        ],
    },
    {
        "train_id": 7,
        "train_no": "10104",
        "name": "Mandovi Express",
        "route_name": "Mumbai Ratnagiri",
        "stops": [
            ("CSMT", None, "15:20", 0, None, 0, 175),
            ("PNVL", "16:15", "16:18", 68, 55, 58, 185),
            ("ROHA", "17:25", "17:28", 143, 125, 128, 190),
            ("CHI", "19:40", "19:43", 319, 260, 263, 185),
            ("RN", "21:50", None, 431, 390, None, None),
        ],
        "landmarks": [
            (3, "R", 8, "sea", "Arabian Sea opens on the right before Chiplun", 40),
            (4, "R", 9, "sea", "Ratnagiri cliffs above the water", 25),
        ],
    },
    {
        "train_id": 8,
        "train_no": "10112",
        "name": "Konkan Kanya Express",
        "route_name": "Madgaon Mumbai",
        "stops": [
            ("MAO", None, "06:50", 0, None, 0, 10),
            ("THVM", "07:55", "07:58", 90, 65, 68, 350),
            ("KUDL", "09:05", "09:08", 170, 135, 138, 355),
            ("KKW", "09:55", "10:00", 230, 185, 190, 0),
            ("RN", "11:15", "11:20", 320, 265, 270, 5),
            ("CHI", "12:45", "12:48", 430, 355, 358, 10),
            ("ROHA", "14:55", "14:58", 600, 485, 488, 15),
            ("PNVL", "16:10", "16:13", 690, 560, 563, 20),
            ("CSMT", "17:40", None, 765, 650, None, None),
        ],
        "landmarks": [
            (4, "L", 9, "sea", "Going north, the sea stays on the left", 20),
            (5, "R", 6, "river", "Savitri river on the climb to Roha", 30),
        ],
    },
    {
        "train_id": 9,
        "train_no": "16209",
        "name": "Udyan Express",
        "route_name": "Mumbai Bengaluru",
        "stops": [
            ("CSMT", None, "20:10", 0, None, 0, 150),
            ("PUNE", "23:40", "23:55", 192, 210, 225, 120),
            ("MRJ", "02:20", "02:30", 392, 370, 380, 110),
            ("BGM", "04:15", "04:25", 500, 485, 495, 130),
            ("UBL", "06:05", "06:20", 610, 595, 610, 140),
            ("SBC", "11:30", None, 840, 920, None, None),
        ],
        "landmarks": [
            (1, "R", 8, "ghat", "Night ghats between Mumbai and Pune, on the right", 90),
            (5, "L", 4, "plain", "Deccan plateau into Bengaluru", 80),
        ],
    },
    {
        "train_id": 10,
        "train_no": "16589",
        "name": "Rani Chennamma Express",
        "route_name": "Bengaluru Pune",
        "stops": [
            ("SBC", None, "06:00", 0, None, 0, 310),
            ("UBL", "11:10", "11:25", 230, 310, 325, 300),
            ("BGM", "13:05", "13:15", 340, 425, 435, 290),
            ("MRJ", "14:55", "15:05", 448, 535, 545, 300),
            ("PUNE", "18:30", None, 648, 750, None, None),
        ],
        "landmarks": [
            (1, "R", 5, "plain", "Open country after the Bengaluru yard", 40),
            (4, "L", 4, "city", "Approach to Pune", 40),
        ],
    },
    {
        "train_id": 11,
        "train_no": "56216",
        "name": "Chamundi Express",
        "route_name": "Bengaluru Mysuru",
        "stops": [
            ("SBC", None, "07:15", 0, None, 0, 250),
            ("MYA", "08:22", "08:24", 92, 67, 69, 255),
            ("MYS", "09:30", None, 139, 135, None, None),
        ],
        "landmarks": [
            (2, "L", 6, "hill", "Chamundi hill on the left before Mysuru", 20),
        ],
    },
    {
        "train_id": 12,
        "train_no": "56642",
        "name": "Malnad Express",
        "route_name": "Mangaluru Bengaluru",
        "stops": [
            ("MAJN", None, "08:00", 0, None, 0, 90),
            ("KBPR", "09:15", "09:18", 62, 75, 78, 95),
            ("SBHR", "10:20", "10:24", 112, 140, 144, 100),
            ("SKLR", "12:40", "12:50", 182, 280, 290, 95),
            ("HAS", "14:20", "14:30", 242, 380, 390, 90),
            ("MYS", "16:05", "16:15", 333, 485, 495, 80),
            ("MYA", "17:10", "17:12", 380, 550, 552, 70),
            ("SBC", "18:25", None, 472, 625, None, None),
        ],
        "landmarks": [
            (3, "R", 10, "ghat", "Climbing the Green Route, ghats on the right", 30),
            (4, "L", 7, "lake", "Hemavati reservoir after the climb", 25),
        ],
    },
    {
        "train_id": 13,
        "train_no": "17317",
        "name": "Matsyagandha Express",
        "route_name": "Madgaon Mangaluru",
        "stops": [
            ("MAO", None, "09:10", 0, None, 0, 160),
            ("KAWR", "10:45", "10:50", 95, 95, 100, 170),
            ("UD", "12:35", "12:40", 185, 205, 210, 175),
            ("MAJN", "14:15", None, 260, 305, None, None),
        ],
        "landmarks": [
            (1, "R", 8, "sea", "Coast south of Goa, sea on the right", 25),
            (2, "R", 7, "sea", "Udupi coast still on the right", 20),
        ],
    },
    {
        "train_id": 14,
        "train_no": "17318",
        "name": "Matsyagandha Express",
        "route_name": "Mangaluru Madgaon",
        "stops": [
            ("MAJN", None, "15:00", 0, None, 0, 340),
            ("UD", "16:30", "16:35", 75, 90, 95, 350),
            ("KAWR", "18:15", "18:20", 165, 195, 200, 355),
            ("MAO", "19:50", None, 260, 290, None, None),
        ],
        "landmarks": [
            (2, "L", 8, "sea", "Going north, the sea is on the left", 20),
        ],
    },
    {
        "train_id": 15,
        "train_no": "16590",
        "name": "Rani Chennamma Express",
        "route_name": "Pune Bengaluru",
        "stops": [
            ("PUNE", None, "07:40", 0, None, 0, 120),
            ("MRJ", "10:50", "11:00", 200, 190, 200, 140),
            ("BGM", "12:40", "12:50", 308, 300, 310, 150),
            ("UBL", "14:30", "14:45", 416, 410, 425, 140),
            ("SBC", "19:40", None, 648, 720, None, None),
        ],
        "landmarks": [
            (1, "R", 5, "plain", "Deccan country after Pune, on the right", 40),
            (4, "L", 4, "city", "Approach to Bengaluru", 50),
        ],
    },
]

GIVEN = [
    "Anil", "Bina", "Chirag", "Diya", "Esha", "Farhan", "Gita", "Hari",
    "Indu", "Jay", "Kavya", "Lakshmi", "Mohan", "Nita", "Omkar", "Pallavi",
    "Qadir", "Rekha", "Sanjay", "Tara", "Uma", "Vikram", "Wasim", "Xavier",
    "Yamini", "Zara", "Arun", "Beena", "Cyrus", "Deepa",
]
FAMILY = ["Rao", "Iyer", "Khan", "Das", "Patil", "Nair", "Shah", "Menon", "Ghosh", "Kulkarni"]
