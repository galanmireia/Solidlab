package DIP;

import java.util.HashMap;
import java.util.Map;


import DIP.Symptom;

	public class Covid19Pacient extends Pacient {
		
		public Covid19Pacient(String name, int years) {
			super(name, years);
			// TODO Auto-generated constructor stub
		}
		double calcCovid19Impact(Afection ca, Increment incr, Impact inpt, 	Map<Symptom, Integer> Symptoms) {
			double afection=0;
			double increment=0;
			double impact;
			//calculate CalcAfection
			afection=ca.calculateSympomsAfection (Symptoms);
			//calculate increment
		
			increment=incr.calculateIncrementPerYear (getYears(), afection);
			//calculate impact
			impact=inpt.calculateImpact(getYears(), afection, increment);
			return impact;
			}

		String name;
		Map<Symptom, Integer> symptoms = new HashMap<Symptom, Integer>();

		public void addSymptom(Symptom p, Integer w) {
			symptoms.put(p, w);
		}

		public void showSymptoms() {
			for (Symptom s : symptoms.keySet())
				s.show();
		}
		public double sanatedDays() {
			double max = 0;
			for (Symptom c : symptoms.keySet()) {
				if (max < c.affectedDays) {
					max = c.affectedDays;
				}
			}
			return max;
		}

	

	

}
