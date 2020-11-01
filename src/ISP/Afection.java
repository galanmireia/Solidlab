package ISP;

import java.util.Map;

import ISP.Symptom;

public class Afection {

	public double calculateSympomsAfection(Map<Symptom,Integer> Symptoms) {
		// TODO Auto-generated method stub
		double afection = 0;
		double elems = 0;
	
		for (Symptom c : Symptoms.keySet()) {
			
		
				afection = afection +c.getSeverityIndex()*Symptoms.get(c);
				elems++;
				}

		afection = afection / (Symptoms.size());
	
	
	return afection;
	}

}
