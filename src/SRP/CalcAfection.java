package SRP;

import java.util.Map;

public class CalcAfection {

	public CalcAfection() {
		
	}

	public double calculateSympomsAfection( Map<Symptom, Integer> symptoms) {
		double afection = 0;
		double elems = 0;
	
		for (Symptom c : symptoms.keySet()) {
			
			if (c.getCovidImpact()>50) {
				afection = afection +c.getSeverityIndex()*symptoms.get(c);
				elems++;
				}
		}
		afection = afection / (symptoms.size());
	
	
	return afection;
	}
}
